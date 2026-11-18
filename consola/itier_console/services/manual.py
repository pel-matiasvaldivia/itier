"""Manual de operaciones de la consola, según el rol del usuario.

Una sola fuente para tres usos: el correo de bienvenida (texto y HTML), la pantalla «Manual» de la
consola y el material de capacitación. El contenido se arma con lo que el rol PUEDE hacer
(services/rbac.py), así un técnico no recibe procedimientos que no puede ejecutar.
"""

from __future__ import annotations

from . import rbac

MISION = {
    "tecnico": "Dar soporte técnico a los equipos de los clientes: resolver tickets, atender las alertas "
               "y anticiparse a los problemas antes de que el cliente los note.",
    "pm": "Llevar la operación diaria de los clientes: altas, despliegue de agentes, calidad del servicio "
          "y seguimiento del equipo técnico.",
    "admin": "Responder por la plataforma: seguridad, usuarios y roles, integraciones (GLPI, Zabbix, MeshCentral) "
             "y continuidad del servicio.",
}

RESPONSABILIDADES = {
    "tecnico": [
        "Atender y actualizar los tickets que se te asignan, dentro de los tiempos acordados con cada cliente; si hay un ticket nuevo sin técnico, tomarlo con «Tomar este caso».",
        "Registrar en cada ticket el tiempo que le dedicaste («Tiempo dedicado»): de ahí salen las horas por cliente y la rentabilidad de cada contrato.",
        "Revisar todos los días la pantalla Equipos: discos con más del 90 % de uso, antivirus apagado o ausente y equipos sin conexión; "
        "resolverlo o abrir un ticket.",
        "Conectarte de forma remota solo con un motivo registrado en el ticket y, si hay una persona usando el equipo, avisándole antes.",
        "Cuidar la confidencialidad de la información de los clientes: nada de capturas, copias ni comentarios fuera de los canales de trabajo.",
        "Mantener tu clave y tu segundo factor (2FA) personales: no se comparten, no se anotan.",
        "Escalar al PM lo que no puedas resolver o cualquier situación que afecte la seguridad del cliente.",
    ],
    "pm": [
        "Dar de alta a los clientes y verificar que queden completos: entidad en GLPI, grupo en Zabbix y grupo de equipos en MeshCentral.",
        "Desplegar los agentes en los equipos de cada cliente y confirmar que reporten (inventario y soporte remoto en línea).",
        "Definir con el cliente la criticidad de sus activos y los rangos de red que se descubren.",
        "Hacer seguimiento de los tickets y de las alertas pendientes, y asignarlos a los técnicos.",
        "Atender la Bandeja: los pedidos por correo o WhatsApp que no se asociaron solos a un cliente; cargar los dominios y las personas de cada cliente para que la próxima vez entren directo.",
        "Revisar la auditoría con regularidad y detectar usos indebidos.",
        "Ser el vínculo con el cliente: comunicar incidentes, entregar el instalador y acompañar la puesta en marcha.",
        "Escalar al Administrador lo que requiera cambios de plataforma, usuarios o permisos.",
        "Cumplir además las responsabilidades del técnico cuando operás equipos.",
    ],
    "admin": [
        "Administrar los usuarios y sus roles con el criterio de mínimo privilegio; dar de baja de inmediato a quien deja de operar.",
        "Custodiar las cuentas de servicio y los secretos (tokens de GLPI y Zabbix, cuenta de Mesh, SMTP): rotarlos y no compartirlos.",
        "Mantener operativos y actualizados GLPI, Zabbix, MeshCentral y la consola, y verificar los respaldos.",
        "Mantener los canales de entrada (pantalla Canales): la cuenta IMAP del correo de soporte y el número de WhatsApp vinculado por QR; si WhatsApp se desvincula, volver a escanear.",
        "Revisar la auditoría y la integridad de su cadena de hash; investigar cualquier anomalía.",
        "Revisar cada mes la pantalla Horas: horas por cliente y por persona, costo y margen; mantener actualizadas las tarifas por hora, la facturación de cada cliente y la cotización del dólar.",
        "Autorizar y ejecutar las bajas de clientes (borra su configuración), sabiendo que es irreversible.",
        "Gestionar los incidentes de seguridad: contener, avisar a los afectados y dejar constancia.",
    ],
}

# funcionalidad → (menú, qué hace). Se filtra por el permiso necesario.
FUNCIONES = [
    ("tablero", "Tablero", "Estado de GLPI y Zabbix, cantidad de clientes y tickets recientes de un vistazo."),
    ("tickets", "Tickets", "Lista, abre y asigna tickets de todos los clientes (la información vive en GLPI)."),
    ("equipos", "Equipos", "Todos los equipos con su conexión, discos, antivirus y alertas; filtrable por cliente y por alertas."),
    ("equipos.remoto", "Conectar", "Abre el control remoto de MeshCentral dentro de la consola, sobre un equipo en línea."),
    ("clientes.ver", "Clientes (ver)", "Ficha de cada cliente y su estado de aprovisionamiento."),
    ("clientes.editar", "Clientes (gestionar)", "Alta de clientes, reintento de aprovisionamiento y usuarios del portal del cliente."),
    ("instaladores", "Instaladores", "Instalador de Windows (enlace por correo, .cmd y .ps1) y estado de los equipos instalados."),
    ("descubrimiento", "Descubrimiento", "Rangos de red y credenciales SNMP para que el inventario encuentre los dispositivos del cliente."),
    ("curacion", "Criticidad", "Asigna a cada activo su criticidad de negocio, que ordena las prioridades."),
    ("agentes", "Agentes", "Vincula hosts de Zabbix a un cliente (monitoreo avanzado)."),
    ("auditoria", "Auditoría", "Bitácora de todo lo que se hizo en la consola, con verificación de integridad."),
    ("usuarios", "Usuarios", "ABM de usuarios de la plataforma: altas con la misma clave en los cuatro sistemas, roles, bajas."),
    ("clientes.eliminar", "Eliminar clientes", "Baja definitiva de un cliente y su configuración."),
]

# (permiso, título, pasos)
PROCEDIMIENTOS = [
    ("tablero", "Primer ingreso y seguridad", [
        "Entrá a la consola con el usuario y la clave temporal que recibiste.",
        "La consola te pide cambiar la clave: elegí una larga y única (la clave temporal se usó en cuatro sistemas, no la reutilices).",
        "Escaneá el código QR con una app de autenticación (Google Authenticator, Microsoft Authenticator o similar) y escribí el código de 6 dígitos: el 2FA es obligatorio.",
        "Ingresá también a MeshCentral con el mismo usuario y clave temporal: te pide cambiarla. Activá el 2FA ahí.",
        "En GLPI, entrá con el mismo usuario y cambiá la clave desde «Mi configuración». Activá el 2FA si está disponible.",
        "Si olvidás la clave o perdés el teléfono con el 2FA, pedile al Administrador «Reiniciar clave».",
    ]),
    ("tickets", "Atender un ticket", [
        "Entrá a Tickets y filtrá los abiertos. Abrí el ticket para ver cliente, descripción y prioridad.",
        "Si no está asignado, asignátelo para que nadie más lo tome.",
        "Resolvelo o pedí los datos que falten al usuario; registrá cada acción en GLPI para que quede el historial.",
        "Si necesitás conectarte al equipo, seguí «Conexión remota» y anotá el motivo en el ticket.",
        "Cuando esté resuelto, avisá al usuario y cerrá el ticket en GLPI con la solución aplicada.",
        "Para un problema que detectaste vos, abrí un ticket nuevo desde Tickets → Nuevo, eligiendo el cliente.",
    ]),
    ("equipos", "Revisar equipos y alertas (todos los días)", [
        "Entrá a Equipos y activá «Solo con alertas»: queda la lista de lo que hay que atender, de más a menos grave.",
        "Disco ≥ 95 % es alerta alta y ≥ 90 % media: liberá espacio (archivos temporales, papelera, copias viejas) o pedí ampliar el disco.",
        "Antivirus OFF es alerta alta: conectate y reactivalo; si no se puede, abrí un ticket urgente al cliente. «Ninguno» (media) significa que no se detectó antivirus.",
        "«Sin conexión ahora» es informativa: si dura más de un día, consultá con el cliente si el equipo está apagado o dado de baja.",
        "Los discos y el antivirus salen del último inventario (normalmente diario); el botón «Actualizar» vuelve a leer los datos, no fuerza un inventario nuevo.",
        "Por cada alerta que atendés, dejá constancia en un ticket.",
    ]),
    ("equipos.remoto", "Conexión remota a un equipo", [
        "En Equipos, ubicá el equipo en línea y tocá «Conectar»: se abre MeshCentral dentro de la consola.",
        "La primera vez iniciá sesión en MeshCentral con tu usuario; la sesión queda guardada.",
        "Si hay alguien usando el equipo, avisale antes de conectarte y explicale qué vas a hacer.",
        "Trabajá solo en lo que dice el ticket. No copies archivos del cliente a tu equipo salvo que sea imprescindible y lo autorice.",
        "Al terminar, cerrá la sesión remota y registrá en el ticket qué hiciste.",
        "Cada apertura de conexión queda en la auditoría con tu usuario y el equipo.",
        "Si no ves un equipo en MeshCentral, avisá al PM: tu usuario debe pertenecer al grupo SOPORTE.",
    ]),
    ("clientes.editar", "Dar de alta un cliente", [
        "Clientes → «Nuevo cliente»: cargá el nombre. La consola crea sola la entidad en GLPI, el grupo en Zabbix, el grupo de equipos en MeshCentral y las reglas de asignación automática.",
        "Verificá en la ficha que el estado sea «activo» y que no haya avisos. Si algo falló, usá «Reintentar aprovisionamiento» (es seguro repetirlo).",
        "Si el cliente tendrá portal, creá sus usuarios desde la ficha (cada uno ve solo su empresa).",
        "Seguí con «Instalar agentes en los equipos».",
    ]),
    ("instaladores", "Instalar agentes en los equipos del cliente", [
        "Clientes → ficha del cliente → «Instaladores». Revisá que arriba figuren GLPI y Soporte remoto configurados.",
        "En «Enviar al cliente», cargá el correo del contacto y tocá «Generar y enviar»: recibe un enlace (no un adjunto) para descargar el instalador.",
        "Pedile que lo ejecute en cada PC con Windows: acepta el aviso de Windows una vez y el resto es automático. Para distribución masiva, usá el .ps1 por GPO o tu RMM.",
        "Al terminar, el instalador muestra un resumen. En «Equipos instalados» verificá que cada equipo figure con instalación correcta, inventario transmitiendo y soporte remoto en línea.",
        "Si un equipo queda con problemas, abrí su detalle: indica si falló el servicio o la conexión. Si no resolvés, escalá al Administrador.",
        "El monitoreo avanzado con Zabbix (servidores, redes, impresoras) está en «Avanzado» y solo se usa si el cliente lo contrata.",
    ]),
    ("descubrimiento", "Descubrir los dispositivos de la red de un cliente", [
        "Ficha del cliente → «Descubrimiento»: cargá el rango de red (por ejemplo 192.168.1.0/24) y la comunidad SNMP.",
        "El inventario recorre el rango y da de alta impresoras, switches y otros dispositivos.",
        "Revisá los resultados; si un rango dejó de aplicar, usá «Reaplicar».",
    ]),
    ("curacion", "Definir la criticidad de los activos", [
        "Ficha del cliente → «Criticidad»: asigná a cada servidor o equipo su criticidad de negocio (crítica, alta…).",
        "Eso ordena las prioridades de atención y de los tickets automáticos.",
    ]),
    ("auditoria", "Revisar la auditoría", [
        "Entrá a Auditoría: arriba dice si la cadena de hash está íntegra (cada entrada sella la anterior).",
        "Revisá altas y bajas de usuarios, conexiones remotas, descargas de instaladores y cambios de clientes.",
        "Ante algo que no reconozcas o una cadena rota, avisá al Administrador de inmediato.",
    ]),
    ("usuarios", "Administrar usuarios y roles", [
        "Usuarios → «Nuevo usuario»: usuario, nombre, correo y rol. La misma cuenta y clave temporal se crean en la consola, GLPI, Zabbix y MeshCentral.",
        "Dejá marcada la casilla para enviar por correo las credenciales, las responsabilidades del rol y el manual. Si el SMTP no está configurado, copiá la clave que se muestra una sola vez y entregala por un canal seguro.",
        "Elegí el rol con el criterio de mínimo privilegio: Técnico por defecto, PM para quien gestiona clientes, Administrador solo para quien administra la plataforma.",
        "«Reiniciar clave» genera una clave nueva en los cuatro sistemas (en Mesh se recrea la cuenta). Usalo si alguien perdió el acceso.",
        "«Dar de baja» corta el acceso en todos los sistemas de inmediato, aunque tenga la sesión abierta. Hacelo el mismo día que alguien deja de operar.",
        "«Eliminar» borra la cuenta definitivamente: preferí la baja si querés conservar el historial.",
        "En la lista, el estado por sistema te dice si algo falló al crear la cuenta; reintentá con «Reiniciar clave».",
    ]),
    ("clientes.eliminar", "Dar de baja un cliente", [
        "Solo cuando el contrato terminó y está confirmado. Ficha del cliente → «Eliminar»: borra su configuración y sus usuarios del portal.",
        "Es irreversible. Antes, exportá lo que haga falta desde GLPI.",
    ]),
]

REGLAS = [
    "Tu clave y tu 2FA son personales e intransferibles: nunca los compartas ni los anotes.",
    "Todo lo que hacés en la consola queda en la auditoría con tu usuario.",
    "La información de los clientes es confidencial: usala solo para el trabajo y por los canales de la empresa.",
    "Bloqueá la pantalla cada vez que te alejás del puesto.",
    "Ante un incidente de seguridad (clave expuesta, equipo robado, acceso no reconocido) avisá de inmediato al Administrador.",
]

ESCALAMIENTO = {
    "tecnico": "Dudas o problemas que no resolvés: PM. Seguridad o accesos: Administrador.",
    "pm": "Plataforma, permisos o usuarios: Administrador.",
    "admin": "Fallas de plataforma que no podés resolver: proveedor del servicio iTier.",
}


def manual_para(rol: str) -> dict:
    """Manual del rol: misión, responsabilidades, funciones, procedimientos y reglas."""
    if rol not in rbac.ROLES:
        rol = "tecnico"
    return {
        "rol": rol,
        "rol_nombre": rbac.ROLES[rol],
        "mision": MISION[rol],
        "responsabilidades": RESPONSABILIDADES[rol],
        "funciones": [(menu, que) for perm, menu, que in FUNCIONES if rbac.puede(rol, perm)],
        "procedimientos": [(titulo, pasos) for perm, titulo, pasos in PROCEDIMIENTOS if rbac.puede(rol, perm)],
        "reglas": REGLAS,
        "escalamiento": ESCALAMIENTO[rol],
    }
