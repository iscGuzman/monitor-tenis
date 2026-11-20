# Monitor de tenis — Cactus Jack

Revisa cada 15 minutos la página pública `/collections/all` de shop.travisscott.com
(permitida por su robots.txt), detecta tenis nuevos y cambios de disponibilidad,
y avisa por [ntfy](https://ntfy.sh). Solo avisa: la compra se hace a mano.

- `tenis.py` — revisión y avisos
- `plantilla.html` — galería que se publica en GitHub Pages
- `estado.json` — último estado conocido e historial de cambios
