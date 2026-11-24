# Reglas del proyecto iTiers

## Interfaz responsiva (obligatorio en todo cambio de pantalla)
Toda pantalla nueva o modificada de `consola/` debe verse bien en celular (390 px), tablet (820 px), laptop (1366 px) y monitor ancho (1900 px), sin scroll horizontal de la página.
- Usar grillas con `minmax(0, …)` / `auto-fit`, `flex-wrap`, y `min-width:0` en hijos de flex/grid; nada de anchos fijos ni `white-space:nowrap` en contenedores.
- Tablas anchas: envolverlas en `.tscroll` o presentarlas como filas apilables en pantallas chicas.
- Antes de dar por terminado un cambio de UI: `cd consola && tests/responsive.sh` (falla si algo desborda) y mirar una captura a 390 y a 1900 px.
- Mantener `tests/smoke_demo.py` y `tests/test_adapters.py` en verde.
