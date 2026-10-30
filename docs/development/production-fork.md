# Автоматический деплой форка

Форк `losogudok/remnawave-minishop` использует workflow `Production` при push
в `main`. Он вызывает существующие PR checks, Full-stack QA и Security для
того же SHA. Деплой начинается только после успешного завершения всех трёх
workflow. Информационные dependency audit шаги Security сохраняют исходный
`continue-on-error`; остальные ошибки блокируют деплой.

Проверки и deployment job выполняются на GitHub-hosted runners. Деплой
подключается по SSH к публичному адресу `89.250.2.153` от пользователя `normis`;
в локальной сети тот же сервер доступен как `192.168.0.9`. Runner на production
не устанавливается. Используется отдельный ключ деплоя; личный SSH-ключ
администратора в GitHub не передаётся.

В GitHub environment `production` задаются переменные `DEPLOY_HOST` и
`DEPLOY_PORT` (`56943`) и secrets `DEPLOY_SSH_KEY`, `DEPLOY_KNOWN_HOSTS`. Host keys
проверяются по ключам SSH-сервера, полученным через доверенное LAN-соединение.
Окружение допускает только ветку `main`. Публичный SSH-порт должен быть
перенаправлен на SSH этого сервера и доступен GitHub-hosted runners.

Production checkout: `/opt/remnawave-minishop`, ветка `main`, origin
`https://github.com/losogudok/remnawave-minishop.git`. Авторизация для чтения
публичного форка не требуется; SSH-ключ администратора в GitHub не передаётся.

`scripts/deploy-production.sh` проверяет чистоту checkout и текущий HEAD
удалённой `main`, затем делает fast-forward до проверенного SHA. Устаревшие
запуски пропускаются. Параллельные деплои запрещены workflow concurrency и
`flock`; начавшийся деплой не отменяется новым push. GitHub может заменить
ожидающий запуск более новым: итоговая production-версия следует последнему
успешно проверенному HEAD `main`.

Сначала собираются production-образы (требуется минимум 10 GiB свободного места).
Затем останавливаются backend и worker, создаются PostgreSQL dump и копия
`.env` в `backups/pre-deploy-*`, запускаются миграции и `compose up --wait`.
Бэкапы содержат секреты, остаются только на сервере и создаются с закрытыми правами.
Планируйте их перенос и очистку отдельно. Перед изменениями файловых данных
сохраняйте также штатный полный бэкап приложения.

Деплой включает перерыв в работе приложения. При ошибке после остановки
backend/worker workflow остаётся красным; автоматического отката схемы БД нет.
Проверьте логи `docker compose logs --tail=100 migrate backend worker frontend`
и при необходимости восстановите БД из соответствующего dump перед возвратом
к предыдущей версии. Повторный запуск `Production` снова проверяет исходники.

Для повторной проверки и деплоя текущей `main` можно вручную запустить
workflow `Production` в GitHub Actions. Другие ветки не получают доступ
к deployment job.
