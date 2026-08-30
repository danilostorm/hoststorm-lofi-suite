from __future__ import annotations

import os
import threading
import time
from datetime import datetime

from .integrations import check_integration, list_integrations
from .pro_db import add_alert
from .professional import cleanup, create_backup, system_snapshot, watch_scan


class ProfessionalServices:
    def __init__(self):
        self.started = False
        self.integration_state = {}
        self.last_retention = ''
        self.last_backup = ''
        self.last_resource_alert = {}

    def start(self):
        if self.started:
            return
        self.started = True
        threading.Thread(target=self._watch_loop, daemon=True, name='watch-folder').start()
        threading.Thread(target=self._maintenance_loop, daemon=True, name='pro-maintenance').start()
        threading.Thread(target=self._integration_loop, daemon=True, name='platform-api-monitor').start()

    def _watch_loop(self):
        time.sleep(10)
        while True:
            if os.environ.get('HOSTSTORM_WATCH_ENABLED', '1') == '1':
                try:
                    moved = watch_scan()
                    if moved:
                        add_alert('info', 'watch-folder', 'Mídia importada automaticamente', ', '.join(moved[:10]))
                except Exception as e:
                    add_alert('error', 'watch-folder', 'Falha na watch folder', str(e))
            time.sleep(max(10, int(os.environ.get('HOSTSTORM_WATCH_INTERVAL_SECONDS', '30'))))

    def _maintenance_loop(self):
        time.sleep(20)
        while True:
            now = datetime.now()
            day = now.strftime('%Y-%m-%d')
            try:
                hour = int(os.environ.get('HOSTSTORM_RETENTION_HOUR', '3'))
                if now.hour == hour and self.last_retention != day:
                    removed = cleanup(
                        int(os.environ.get('HOSTSTORM_LOG_RETENTION_DAYS', '30')),
                        int(os.environ.get('HOSTSTORM_BACKUP_KEEP', '20')),
                        int(os.environ.get('HOSTSTORM_RECORDING_RETENTION_DAYS', '30')),
                        int(os.environ.get('HOSTSTORM_CLIP_RETENTION_DAYS', '60')),
                    )
                    self.last_retention = day
                    if removed:
                        add_alert('info', 'retention', 'Retenção automática concluída', f'{len(removed)} arquivo(s) removido(s).')

                backup_hour = int(os.environ.get('HOSTSTORM_BACKUP_HOUR', '4'))
                if now.hour == backup_hour and self.last_backup != day:
                    p = create_backup('daily')
                    self.last_backup = day
                    add_alert('info', 'backup', 'Backup diário concluído', p.name)
            except Exception as e:
                add_alert('error', 'maintenance', 'Falha na manutenção automática', str(e))

            try:
                self._resource_checks()
            except Exception:
                pass
            time.sleep(300)

    def _resource_checks(self):
        snapshot = system_snapshot()
        now = time.time()
        checks = [
            ('cpu', snapshot['cpu'], float(os.environ.get('HOSTSTORM_CPU_ALERT', '92')), 'CPU alta'),
            ('ram', snapshot['ram'], float(os.environ.get('HOSTSTORM_RAM_ALERT', '92')), 'RAM alta'),
            ('disk', snapshot['disk_percent'], float(os.environ.get('HOSTSTORM_DISK_ALERT', '90')), 'Disco quase cheio'),
        ]
        for key, value, limit, title in checks:
            if value >= limit and now - self.last_resource_alert.get(key, 0) > 3600:
                self.last_resource_alert[key] = now
                add_alert('warning', 'resources', title, f'{value:.1f}% (limite {limit:.0f}%)')

    def _integration_loop(self):
        time.sleep(25)
        while True:
            for item in list_integrations(mask=False):
                if not item.get('enabled'):
                    continue
                result = check_integration(item['id'])
                previous = self.integration_state.get(item['id'])
                current = (result.get('ok'), result.get('live'))
                if previous is not None and previous != current:
                    severity = 'info' if result.get('ok') else 'warning'
                    add_alert(
                        severity,
                        'platform-api',
                        f"{item['provider'].upper()} • {item['name']}",
                        result.get('message', 'mudança de estado'),
                    )
                self.integration_state[item['id']] = current
            time.sleep(max(30, int(os.environ.get('HOSTSTORM_PLATFORM_API_INTERVAL_SECONDS', '60'))))


SERVICES = ProfessionalServices()
