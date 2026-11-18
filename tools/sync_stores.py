"""
Allinea catalog.json agli store: Steam, App Store, Google Play.

    python tools/sync_stores.py            # aggiorna catalog.json e i trailer, non pubblica
    python tools/sync_stores.py --push     # ...e fa commit e push (online in 1-2 minuti)
    python tools/sync_stores.py --dry      # solo il resoconto, non scrive niente

Cosa fa da solo:
- Steam: data di uscita, "in arrivo" che diventa uscito, trailer per lingua (italiano e
  inglese, scelti tra i video della pagina Steam come li vede chi ha Steam in quella lingua)
  con l'audio estratto in OGG (PC) e MP3 (mobile).
- App Store: prezzo in euro, app tolte dalla vendita.
- Google Play: pagine che non esistono piu.
- Giochi nuovi trovati negli store e non ancora nel catalogo ne in sources.json/ignore:
  aggiunti NASCOSTI ("hidden": true, "needsText": true) con i testi dello store come bozza.
  Diventano visibili solo quando qualcuno (l'azione pianificata di Claude) scrive titolo e
  descrizione breve e toglie hidden. Regola dei testi: mai dire che il gioco e accessibile o
  per non vedenti: gli audiogiochi lo sono per natura.

Il resoconto finale (stdout, e tools/last_sync.txt) dice cosa e cambiato e cosa aspetta una
decisione.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
CATALOG = os.path.join(ROOT, "catalog.json")
SOURCES = os.path.join(ROOT, "tools", "sources.json")
DRY = "--dry" in sys.argv
PUSH = "--push" in sys.argv
UA = {"User-Agent": "audiogame-catalog-sync/1.0"}
report, todo = [], []


def get(url, as_json=True, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=20) as r:
                data = r.read()
                return json.loads(data.decode("utf-8")) if as_json else data.decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            if i == tries - 1:
                print("  ! rete:", url, e)
                return None
            time.sleep(2 + i * 3)


def status(url):
    try:
        req = urllib.request.Request(url, headers=UA, method="GET")
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:  # noqa: BLE001
        return 0


# ---------------- Steam ----------------

def steam_details(appid, lang):
    d = get(f"https://store.steampowered.com/api/appdetails?appids={appid}&cc=it&l={lang}")
    time.sleep(1.2)  # Steam limita le richieste
    if not d or not d.get(str(appid), {}).get("success"):
        return None
    return d[str(appid)]["data"]


def steam_search(field, name):
    q = urllib.parse.quote(name)
    d = get(f"https://store.steampowered.com/search/results/?{field}={q}&json=1&count=100&ignore_preferences=1")
    out = {}
    for it in (d or {}).get("items", []):
        m = re.search(r"/apps/(\d+)/", it.get("logo", ""))
        if m:
            out[m.group(1)] = it.get("name")
    return out


LANG_HINT = {"it": re.compile(r"\b(ita|italian|italiano)\b", re.I), "en": re.compile(r"\b(eng|english|inglese)\b", re.I)}


def pick_trailer(movies_by_lang):
    """Per ogni lingua il video piu adatto: col nome nella lingua, poi uno visto solo in
    quella lingua (Steam mostra trailer localizzati), poi il primo. Il trailer "Audio" e
    preferito al gameplay quando e lo stesso per tutte le lingue."""
    ids = {l: [m["id"] for m in ms] for l, ms in movies_by_lang.items()}
    chosen = {}
    for lang, ms in movies_by_lang.items():
        if not ms:
            continue
        others = set(i for l, v in ids.items() if l != lang for i in v)
        named = [m for m in ms if LANG_HINT[lang].search(m["name"])]
        own = [m for m in ms if m["id"] not in others]
        other_lang_named = [m for m in ms if any(LANG_HINT[o].search(m["name"]) for o in LANG_HINT if o != lang)]
        pool = named or own or [m for m in ms if m not in other_lang_named] or ms
        chosen[lang] = pool[0]
    return chosen


def extract_trailer(game_id, movie):
    rel = f"trailer/{game_id}_{movie['id']}"
    ogg, mp3 = os.path.join(ROOT, rel + ".ogg"), os.path.join(ROOT, rel + ".mp3")
    if os.path.exists(ogg) and os.path.exists(mp3):
        return rel
    if DRY:
        return rel
    src = movie.get("hls_h264") or (movie.get("mp4") or {}).get("max")
    if not src:
        return None
    wav = os.path.join(ROOT, "trailer", f"_tmp_{game_id}.wav")
    try:
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", src, "-vn", "-t", "120",
                        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", "44100", "-ac", "2", wav], check=True)
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", wav, "-c:a", "libvorbis", "-q:a", "4", ogg], check=True)
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", wav, "-c:a", "libmp3lame", "-b:a", "128k", mp3], check=True)
    except Exception as e:  # noqa: BLE001
        print("  ! trailer non estratto:", game_id, e)
        return None
    finally:
        if os.path.exists(wav):
            os.remove(wav)
    report.append(f"trailer nuovo: {rel} ({movie['name']})")
    return rel


MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def parse_steam_date(s):
    s = (s or "").strip().lower().replace(",", "")
    m = re.match(r"(\d{1,2}) (\w{3})\w* (\d{4})", s) or None
    if m:
        return date(int(m.group(3)), MONTHS.get(m.group(2)[:3], 1), int(m.group(1)))
    m = re.match(r"(\w{3})\w* (\d{1,2}) (\d{4})", s)
    if m:
        return date(int(m.group(3)), MONTHS.get(m.group(1)[:3], 1), int(m.group(2)))
    m = re.match(r"q([1-4]) (\d{4})", s)
    if m:
        q = int(m.group(1))
        return date(int(m.group(2)), q * 3, 28)
    m = re.match(r"(\d{4})", s)
    if m:
        return date(int(m.group(1)), 12, 31)
    return None


def release_text(steam_en):
    s = (steam_en or "").strip()
    m = re.match(r"Q([1-4]) (\d{4})", s, re.I)
    if m:
        q = int(m.group(1))
        it = ["primo", "secondo", "terzo", "quarto"][q - 1]
        en = ["first", "second", "third", "fourth"][q - 1]
        return f"{it} trimestre {m.group(2)}", f"{en} quarter of {m.group(2)}"
    d = parse_steam_date(s)
    if d and re.search(r"\d{1,2}", s.split()[0] if s else ""):
        mesi = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]
        return f"{d.day} {mesi[d.month - 1]} {d.year}", d.strftime("%B %d, %Y").replace(" 0", " ")
    return s, s


def sync_steam(cat, src):
    known = {str(g["stores"]["steam"]["appId"]): g for g in cat["games"] if "steam" in g.get("stores", {})}
    for appid, g in known.items():
        en = steam_details(appid, "english")
        it = steam_details(appid, "italian")
        if not en:
            todo.append(f"Steam {appid} ({g['id']}): pagina non leggibile, controllare")
            continue
        rd = en.get("release_date", {})
        soon = bool(rd.get("coming_soon"))
        if soon != bool(g.get("comingSoon")):
            report.append(f"{g['id']}: {'in arrivo' if soon else 'USCITO'}")
            g["comingSoon"] = soon
            if not soon:
                g.pop("releaseText_it", None)
                g.pop("releaseText_en", None)
        if rd.get("date") and rd.get("date") != g.get("steamDate"):
            d = parse_steam_date(rd["date"])
            if d:
                g["releaseDate"] = d.isoformat()
            if soon:
                g["releaseText_it"], g["releaseText_en"] = release_text(rd["date"])
            g["steamDate"] = rd["date"]
            report.append(f"{g['id']}: data Steam {rd['date']}")
        if not soon and not g.get("comingSoon"):
            g.pop("comingSoon", None)

        movies = {"it": (it or {}).get("movies", []), "en": en.get("movies", [])}
        chosen = pick_trailer(movies)
        if chosen:
            tr = {}
            for lang, mv in chosen.items():
                rel = extract_trailer(g["id"], mv)
                if rel:
                    tr[lang] = rel
            if tr and tr != g.get("trailer"):
                g["trailer"] = tr
                report.append(f"{g['id']}: trailer " + ", ".join(f"{l}={v}" for l, v in tr.items()))

    # giochi nuovi
    found = {}
    for p in src["steamPublishers"]:
        found.update(steam_search("publisher", p))
    for d in src["steamDevelopers"]:
        found.update(steam_search("developer", d))
    for appid, name in sorted(found.items()):
        if appid in known or appid in src["steamIgnore"]:
            continue
        det = steam_details(appid, "english")
        if not det or det.get("type") != "game":
            todo.append(f"Steam {appid} {name}: non e un gioco (tipo {det and det.get('type')}), aggiungerlo a steamIgnore")
            continue
        detit = steam_details(appid, "italian") or det
        gid = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:30]
        rd = det.get("release_date", {})
        d = parse_steam_date(rd.get("date"))
        cat["games"].append({
            "id": gid, "hidden": True, "needsText": True,
            "releaseDate": (d or date.today()).isoformat(),
            "steamDate": rd.get("date"),
            **({"comingSoon": True} if rd.get("coming_soon") else {}),
            "title": {"it": name, "en": name},
            "short": {"it": detit.get("short_description", ""), "en": det.get("short_description", "")},
            "stores": {"steam": {"appId": int(appid)}},
        })
        todo.append(f"NUOVO su Steam: {name} ({appid}) aggiunto nascosto: decidere se e un audiogioco, scrivere i testi o metterlo in steamIgnore")


# ---------------- App Store ----------------

def sync_ios(cat, src):
    games_ios = {str(g["stores"]["ios"]["appId"]): g for g in cat["games"] if "ios" in g.get("stores", {})}
    if games_ios:
        d = get("https://itunes.apple.com/lookup?country=it&id=" + ",".join(games_ios))
        alive = {str(r["trackId"]): r for r in (d or {}).get("results", []) if "trackId" in r}
        if d is not None:
            for tid, g in games_ios.items():
                r = alive.get(tid)
                st = g["stores"]["ios"]
                if not r:
                    # Non in vendita (tolta, in revisione): il catalogo non la mostra finche non torna.
                    if st.get("available", True):
                        st["available"] = False
                        report.append(f"{g['id']}: iOS non in vendita, nascosto")
                    continue
                if st.pop("available", True) is False:
                    report.append(f"{g['id']}: iOS di nuovo in vendita")
                if r.get("price") is not None and st.get("price", {}).get("EUR") != r["price"]:
                    st.setdefault("price", {})["EUR"] = r["price"]
                    report.append(f"{g['id']}: prezzo iOS {r['price']} EUR")
                if r.get("bundleId") and st.get("bundleId") != r["bundleId"]:
                    st["bundleId"] = r["bundleId"]
    for artist in src["iosArtistIds"]:
        d = get(f"https://itunes.apple.com/lookup?id={artist}&entity=software&country=it&limit=200")
        for r in (d or {}).get("results", []):
            tid = str(r.get("trackId", ""))
            if not tid or r.get("wrapperType") != "software" or tid in games_ios or tid in src["iosIgnore"]:
                continue
            gid = re.sub(r"[^a-z0-9]+", "_", r["trackName"].lower()).strip("_")[:30]
            cat["games"].append({
                "id": gid, "hidden": True, "needsText": True,
                "releaseDate": r.get("releaseDate", "2000-01-01")[:10],
                "title": {"it": r["trackName"], "en": r["trackName"]},
                "short": {"it": r.get("description", "")[:300], "en": r.get("description", "")[:300]},
                "stores": {"ios": {"appId": int(tid), "bundleId": r.get("bundleId"), "price": {"EUR": r.get("price", 0)}}},
            })
            todo.append(f"NUOVO su App Store: {r['trackName']} ({tid}) aggiunto nascosto: testi da scrivere o iosIgnore")


# ---------------- Google Play ----------------

def sync_play(cat, src):
    for g in cat["games"]:
        a = g.get("stores", {}).get("android")
        if not a:
            continue
        code = status(f"https://play.google.com/store/apps/details?id={a['package']}&hl=it")
        if code == 404 and a.get("available", True):
            a["available"] = False
            report.append(f"{g['id']}: Google Play non disponibile (404), nascosto")
        elif code == 200 and a.pop("available", True) is False:
            report.append(f"{g['id']}: Google Play di nuovo disponibile")
    page = get(f"https://play.google.com/store/apps/dev?id={src['playDeveloperId']}&hl=it", as_json=False) or ""
    pkgs = set(re.findall(r"details\?id=([A-Za-z0-9._]+)", page))
    known = {g["stores"]["android"]["package"] for g in cat["games"] if "android" in g.get("stores", {})}
    for p in sorted(pkgs - known - set(src["playIgnore"])):
        todo.append(f"NUOVO su Google Play: {p}: collegarlo al gioco giusto (stores.android) o metterlo in playIgnore")


def main():
    cat = json.load(open(CATALOG, encoding="utf-8"))
    src = json.load(open(SOURCES, encoding="utf-8"))
    before = json.dumps(cat, ensure_ascii=False, sort_keys=True)

    sync_steam(cat, src)
    sync_ios(cat, src)
    sync_play(cat, src)

    for g in cat["games"]:
        if g.get("needsText") and not g.get("hidden"):
            todo.append(f"{g['id']}: needsText ancora presente")
    changed = json.dumps(cat, ensure_ascii=False, sort_keys=True) != before
    if changed:
        cat["updated"] = date.today().isoformat()

    lines = [f"Sincronizzazione {datetime.now():%Y-%m-%d %H:%M}", "", "Cambiato:"] + (["- " + r for r in report] or ["- niente"])
    lines += ["", "Da decidere:"] + (["- " + t for t in todo] or ["- niente"])
    out = "\n".join(lines)
    print(out)
    if DRY:
        return
    with open(os.path.join(ROOT, "tools", "last_sync.txt"), "w", encoding="utf-8") as f:
        f.write(out + "\n")
    if changed:
        with open(CATALOG, "w", encoding="utf-8", newline="\n") as f:
            json.dump(cat, f, ensure_ascii=False, indent=2)
            f.write("\n")
        json.load(open(CATALOG, encoding="utf-8"))  # controllo
    # trailer non piu usati
    used = set()
    for g in cat["games"]:
        t = g.get("trailer")
        used.update(t.values() if isinstance(t, dict) else ([t] if t else []))
    tdir = os.path.join(ROOT, "trailer")
    for f in os.listdir(tdir):
        rel = "trailer/" + os.path.splitext(f)[0]
        if rel not in used and not f.startswith("_tmp"):
            os.remove(os.path.join(tdir, f))
            print("  trailer tolto:", f)
    if PUSH:
        subprocess.run(["git", "-C", ROOT, "add", "-A"], check=True)
        r = subprocess.run(["git", "-C", ROOT, "diff", "--cached", "--quiet"])
        if r.returncode != 0:
            msg = "Sincronizzazione con gli store " + date.today().isoformat() + "\n\n" + "\n".join("- " + x for x in report[:40])
            subprocess.run(["git", "-C", ROOT, "commit", "-q", "-m", msg + "\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"], check=True)
            subprocess.run(["git", "-C", ROOT, "push", "-q"], check=True)
            print("pubblicato")


if __name__ == "__main__":
    main()
