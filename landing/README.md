# Landing de iTier

Sitio estático de una sola página (`index.html`) para
**itier.pymesenlinea.com.ar**. Tema claro, sin dependencias externas: todo el
CSS y JS va embebido, así que funciona servido por cualquier servidor estático.

## Contenido

- `index.html` — la landing completa (autónoma).
- `manual.html` — el [manual de operaciones](../docs/07-manual-operativo.md)
  renderizado como página HTML, con el mismo diseño. Se enlaza desde el pie de la
  landing. **Generado** (no editar a mano): ver [Regenerar el manual](#regenerar-el-manual).
- `build-manual.py` — generador de `manual.html` a partir del markdown.
- `docker-compose.yml` — nginx mínimo para servir ambas páginas detrás de NPM.

## Desplegar detrás de Nginx Proxy Manager

NPM corre en **otro VPS de la LAN**, así que la landing publica su puerto en el
host (por defecto `8082`) y NPM lo alcanza por IP:puerto.

```sh
cd landing
# opcional: atá el puerto a la IP de la LAN de este host
echo "LANDING_BIND=<IP-LAN-de-este-host>" > .env
docker compose up -d
```

Luego, en la UI de NPM (en el otro VPS):

1. **Proxy Hosts → Add Proxy Host.**
2. *Domain Names:* `itier.pymesenlinea.com.ar`
3. *Forward Hostname / IP:* `<IP-LAN-de-este-host>` · *Forward Port:* `8082`
   (o el `LANDING_PORT` que hayas fijado).
4. Activá **Block Common Exploits** y **Websockets** si querés.
5. Pestaña **SSL:** *Request a new SSL Certificate* + *Force SSL* + *HTTP/2*.

> **Firewall.** Limitá el puerto `8082` a la IP del VPS de NPM. Si la landing
> corre en el **mismo** VPS que NPM, podés apuntar el Proxy Host a
> `http://127.0.0.1:8082`. Si la LAN no es de confianza, tunelizá con WireGuard.

### Cabeceras y caché (opcional)

Se pueden agregar desde NPM, en la pestaña **Advanced** del Proxy Host:

```nginx
add_header X-Content-Type-Options nosniff;
add_header X-Frame-Options SAMEORIGIN;
add_header Referrer-Policy strict-origin-when-cross-origin;
```

## Editar el contenido

Todo el texto y los estilos están en `index.html`. Puntos que quizá quieras
ajustar:

- **Correo de contacto:** buscá `matiasvaldivia@pymesenlinea.com.ar` y
  reemplazalo por tu alias comercial (ej. `ventas@…`) si tenés uno.
- **Botón "Hablar con un especialista":** el `href="tel:"` está vacío; poné tu
  número (`tel:+54911...`) o cambialo por otro `mailto:`.
- **Paleta:** los colores están en las variables CSS `:root` (arriba de todo).

Tras editar, `docker compose restart landing` (o recargá: el archivo está
montado, no copiado).

## Regenerar el manual

`manual.html` se genera desde `docs/07-manual-operativo.md`. Si editás el manual,
regeneralo:

```sh
pip install markdown        # única dependencia, solo para regenerar
python3 landing/build-manual.py
```

El HTML resultante es autónomo (sin dependencias) y conserva los anclajes internos
del documento. Los enlaces a otros archivos del repo (ADRs, deploy, políticas) se
muestran como referencias no navegables, para no dejar enlaces rotos en el sitio
público.

> **Visibilidad.** El manual es un documento **interno de operaciones**. Lleva
> `noindex` para no aparecer en buscadores, pero si está enlazado desde la landing
> es accesible por URL. Si no querés que sea público, protegé la ruta `/manual.html`
> en NPM (pestaña **Advanced**) con *Access List* (usuario/clave) o quitá el enlace
> del pie de `index.html` y serví el manual en otro Proxy Host restringido.

## Servir sin Docker

Al ser un único HTML autónomo, también podés subirlo a cualquier hosting
estático, apuntar un `nginx`/`caddy` a la carpeta, o abrirlo directo en el
navegador para previsualizarlo.
