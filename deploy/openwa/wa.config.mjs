// Configuración de OpenWA v5 para iTier. Todo lo variable viene de variables de entorno.
// El webhook entrega cada mensaje entrante a la consola (POST con el token en la URL).
const url = process.env.ITIER_WEBHOOK_URL;
const secret = process.env.ITIER_WEBHOOK_SECRET;

export default {
  // Chrome dentro de Docker: sin sandbox del kernel (el contenedor ya es el aislamiento).
  chromiumArgs: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage'],
  allowDangerousBrowserArgs: true,
  plugins: url ? ['@open-wa/integration-webhook'] : [],
  pluginConfig: url ? {
    webhook: {
      url,
      events: ['message.received'],
      headers: secret ? { 'X-Webhook-Secret': secret } : {},
      retries: 5,
      retryDelay: 2000,
      timeout: 15000,
    },
  } : {},
};
