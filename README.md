# Feed del catalogo audiogame IV Productions

Pubblicato con GitHub Pages su https://catalog.ivproductions.it/ e letto dal pacchetto Unity
`com.ivproductions.catalog` dentro ogni audiogame. Cambiare questi file cambia il catalogo in
tutti i giochi, senza ribuildarli.

- `catalog.json`: giochi, pagine, frasi, suoni. Progetto completo in
  `Dropbox/docs/_AUDIOGAME_IVP/_CATALOGO_COMPONENTE/PROGETTO.md`.
- `ui/`, `amb/`: suoni (ogni file in `.ogg` per PC e `.mp3` per mobile), generati da `tools/make_sounds.py`.
- `trailer/`: audio dei trailer, in streaming.
- **Regola della cache**: i client tengono i file per nome e non li riscaricano. Per cambiare
  un suono o una voce si usa un nome nuovo (`move_v2`) e lo si scrive in `catalog.json`.
- Un `catalog.json` rotto non rompe i giochi (usano l'ultima copia buona), ma va comunque
  controllato con `python -c "import json;json.load(open('catalog.json',encoding='utf-8'))"` prima del push.
