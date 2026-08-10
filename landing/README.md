# Landing de iTier

Sitio estático de una sola página (`index.html`) para
**itier.pymesenlinea.com.ar**. Tema claro, sin dependencias externas: todo el
CSS y JS va embebido, así que funciona servido por cualquier servidor estático.

## Contenido

- `index.html` — la landing completa (autónoma).
- `docker-compose.yml` — nginx mínimo para servirla detrás de NPM.

## Desplegar detrás de Nginx Proxy Manager

```sh
cd landing
docker compose up -d
```

Luego, en la UI de NPM:

1. **Proxy Hosts → Add Proxy Host.**
2. *Domain Names:* `itier.pymesenlinea.com.ar`
3. *Forward Hostname / IP:* `itier-landing` · *Forward Port:* `80`
   (comparten la red `npm`, así que NPM lo resuelve por nombre).
4. Activá **Block Common Exploits** y **Websockets** si querés.
5. Pestaña **SSL:** *Request a new SSL Certificate* + *Force SSL* + *HTTP/2*.

> Si tu red de NPM no se llama `npm`, exportá `NPM_NETWORK` o creá un `.env`
> con `NPM_NETWORK=<tu-red>` antes de `docker compose up`.

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

## Servir sin Docker

Al ser un único HTML autónomo, también podés subirlo a cualquier hosting
estático, apuntar un `nginx`/`caddy` a la carpeta, o abrirlo directo en el
navegador para previsualizarlo.
