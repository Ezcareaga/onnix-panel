# M3 WhatsApp Templates — Guía de submit a Meta

Templates de Onnix (identidad actualizada), categoría MARKETING.
Requieren aprobación de Meta antes de poder usarse.

## Contexto

Los 10 templates reemplazan versiones anteriores una vez aprobados:

| Key nueva                         | Reemplaza                      |
|-----------------------------------|-------------------------------|
| wa_tpl_ic_welcome_v3              | wa_tpl_ic_welcome_v2           |
| wa_tpl_ic_reenviado_welcome_v3    | wa_tpl_ic_reenviado_welcome_v2 |
| wa_tpl_send_property_v4           | wa_tpl_send_property (v3)      |
| wa_tpl_send_preferences_v4        | wa_tpl_send_preferences (v3)   |
| wa_tpl_send_generic_v3            | wa_tpl_send_generic (v2)       |
| wa_tpl_followup_v3                | wa_tpl_followup (v2)           |
| wa_tpl_followup_72h_v3            | wa_tpl_followup_72h (v2)       |
| wa_tpl_agent_reply_v3             | wa_tpl_agent_reply (v2)        |
| wa_tpl_ic_recurrente_directo_v2   | wa_tpl_ic_recurrente_directo   |
| wa_tpl_ic_recurrente_reenviado_v2 | wa_tpl_ic_recurrente_reenviado |

Los templates anteriores siguen activos hasta que Ez los retire.

---

## Pasos para Ez

### Paso 1 — Revisar textos con la administradora (30 min)

Correr dry-run para ver todos los textos formateados:

```bash
cd /home/onnix
python3 scripts/twilio_create_templates_m3.py --dry-run
```

Muestra cada body con emojis, variables y el payload JSON completo.
Ideal para proyectar en la reunión con la administradora.

Si la administradora pide cambios en algún texto, editar la constante `TEMPLATES`
en `scripts/twilio_create_templates_m3.py` (bloque al inicio del archivo)
y volver a correr `--dry-run` para verificar.

### Paso 2 — Exportar credenciales Twilio

```bash
export TWILIO_ACCOUNT_SID="AC..."
export TWILIO_AUTH_TOKEN="..."
```

Usar las credenciales de PRODUCCION (WABA +595900000000).
No usar credenciales de staging/test para el submit a Meta.

### Paso 3 — Submit a Meta

```bash
python3 scripts/twilio_create_templates_m3.py --submit
```

El script:
- Valida todos los textos antes de llamar a Twilio.
- Por cada template: crea en Content API + somete a ApprovalRequests.
- Loguea cada resultado a `/home/onnix/logs/templates_m3_submit.log`.
- Es idempotente: si una key ya tiene un SID real en bot_settings, la saltea.

Tiempo estimado: ~15 segundos para los 10 templates.

### Paso 4 — Esperar 24-48h

Meta revisa los templates. El proceso es automático.
En caso de rechazo, el motivo aparece en el paso 5.

### Paso 5 — Verificar estado de aprobación (dry-run)

```bash
export TWILIO_ACCOUNT_SID="AC..."
export TWILIO_AUTH_TOKEN="..."
export DATABASE_URL="postgresql://onnix:<pw>@localhost:5432/onnix_prod"

python3 scripts/twilio_update_m3_sids.py
```

Imprime el estado de cada template: `approved`, `pending`, `rejected`.
No modifica nada.

### Paso 6 — Sincronizar SIDs aprobados a bot_settings

```bash
python3 scripts/twilio_update_m3_sids.py --commit
```

Para cada template aprobado, hace:
```sql
UPDATE bot_settings SET value = 'HX...' WHERE key = 'wa_tpl_..._v3';
```

Verificar resultado:
```bash
docker exec onnix-postgres psql -U onnix -d onnix_prod \
  -c "SELECT key, value FROM bot_settings WHERE key LIKE 'wa_tpl_%v3' OR key LIKE 'wa_tpl_%v4' ORDER BY key;"
```

Los templates con SID real ya funcionan. Los que siguen en PLACEHOLDER esperan aprobación.

---

## Troubleshooting

### Error "PLACEHOLDER rejected" en el panel

Normal. `template_service.py` rechaza PLACEHOLDER.
Los templates nuevos no son usables hasta que Meta los aprueba y
se sincroniza el SID con `--commit`.

### Template rechazado por Meta

Meta envía `rejection_reason` en el estado de aprobación.
Opciones:
1. Corregir el texto del template en `TEMPLATES` del script de creación.
2. Crear una versión `_v4` (no re-editar el rechazado).
3. Re-submit con el texto corregido.

### El script falla con "no SID found"

Correr primero `twilio_create_templates_m3.py --submit`.
El script de update lee los SIDs del log de submit.
Si el log fue eliminado, usa `GET /Content` para redescubrirlos.

---

## Archivos relacionados

- `scripts/twilio_create_templates_m3.py` — crea + somete templates
- `scripts/twilio_update_m3_sids.py` — sincroniza SIDs post-aprobación
- `panel/alembic/versions/032_m3_wa_templates.py` — migración con las 10 keys PLACEHOLDER
- `panel/app/schemas/template.py` — ALLOWED_TEMPLATE_KEYS (panel manual-send)
- `/home/onnix/logs/templates_m3_submit.log` — log de submit (JSON por línea)
