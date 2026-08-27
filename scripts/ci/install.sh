#!/usr/bin/env bash
# Instala el pipeline en el VPS desde este checkout. Se corre a mano, con sudo.
#
# Los ejecutables se COPIAN a /usr/local/bin en vez de correrse desde el
# worktree: el pipeline actualiza ese worktree, y un script que se reescribe a
# sí mismo mientras corre es una forma barata de perder una tarde.
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "corrélo con sudo" >&2; exit 1; }
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

# Estado del pipeline. onnixci escribe en la cola; onnix la drena, así que el
# directorio es de los dos. El resto lo escribe solo el pipeline.
install -d -o onnixci     -g onnix -m 775 /var/lib/onnixci
install -d -o onnixci     -g onnix -m 770 /var/lib/onnixci/queue
install -d -o onnix -g onnix -m 755 /var/lib/onnixci/logs
[ -f /var/lib/onnixci/status.json ] || install -o onnix -g onnix -m 644 /dev/null /var/lib/onnixci/status.json

# El mirror lo fetchea onnixci; lo leen onnix (el pipeline) y sus worktrees.
# Sin sharedRepository=group, el primer objeto que escriba otro usuario deja su
# directorio sin permiso de grupo y el fetch de onnixci muere con "insufficient
# permission for adding an object". El 2026-08-18 pasó exactamente eso: el job
# de dev se marcó rojo por "fetch del mirror falló" y staging quedó tres horas
# atrás. Se re-asserta en cada install porque es barato y el síntoma no se
# parece a la causa.
if [ -d /srv/onnix/repo.git ]; then
    # -c safe.directory: install.sh corre como root y el mirror es de onnixci.
    # Sin esto git dice 'not in a git directory', que no se parece en nada
    # a 'este repo es de otro dueño' y corta el install entero.
    git -c safe.directory=/srv/onnix/repo.git -C /srv/onnix/repo.git \
        config core.sharedRepository group
    chown -R onnixci:onnix /srv/onnix/repo.git
    chmod -R g+w /srv/onnix/repo.git
fi

install -o root -g root -m 755 "$HERE/pipeline.sh" /usr/local/bin/onnix-pipeline
install -o root -g root -m 755 "$HERE/enqueue.sh"  /usr/local/bin/onnix-enqueue
install -o root -g root -m 644 "$HERE/onnix-pipeline.service" /etc/systemd/system/onnix-pipeline.service
install -o root -g root -m 644 "$HERE/onnix-pipeline.path"    /etc/systemd/system/onnix-pipeline.path
install -o root -g root -m 644 "$HERE/onnix-pipeline.timer"   /etc/systemd/system/onnix-pipeline.timer
install -d -o root -g root -m 755 /etc/systemd/system/webhook.service.d
install -o root -g root -m 644 "$HERE/webhook-override.conf" /etc/systemd/system/webhook.service.d/override.conf
install -o root -g root -m 755 "$HERE/github-hooks-allow.sh" /usr/local/bin/onnix-github-ips
install -o root -g root -m 644 "$HERE/onnix-github-ips.service" /etc/systemd/system/onnix-github-ips.service
install -o root -g root -m 644 "$HERE/onnix-github-ips.timer"   /etc/systemd/system/onnix-github-ips.timer
install -o root -g root -m 755 "$HERE/drift-check.sh" /usr/local/bin/onnix-drift
install -o root -g root -m 644 "$HERE/onnix-drift.service" /etc/systemd/system/onnix-drift.service
install -o root -g root -m 644 "$HERE/onnix-drift.timer"   /etc/systemd/system/onnix-drift.timer

# hooks.json lleva el secreto del HMAC, que no está en git. Si ya existe uno
# instalado, se conserva el secreto y se actualiza el resto.
if [ -f /etc/onnixci/hooks.json ]; then
    secreto=$(grep -oP '(?<="secret": ")[^"]+' /etc/onnixci/hooks.json | head -1)
else
    secreto=$(openssl rand -hex 32)
    echo "secreto nuevo generado — cargalo en el webhook de GitHub:"
    echo "$secreto"
fi
sed "s|__SECRETO_VA_ACA__|$secreto|" "$HERE/hooks.json" > /etc/onnixci/hooks.json
chown onnixci:onnixci /etc/onnixci/hooks.json
chmod 600 /etc/onnixci/hooks.json

systemctl daemon-reload
systemctl restart webhook.service
systemctl enable --now onnix-pipeline.path
systemctl enable --now onnix-pipeline.timer
systemctl enable --now onnix-github-ips.timer
systemctl enable --now onnix-drift.timer
systemctl --no-pager --lines=0 status webhook.service onnix-pipeline.path | grep -E 'Loaded|Active'
