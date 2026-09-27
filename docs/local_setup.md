# Preparación local reproducible

Objetivo verificado: Windows x64, Python 3.13.0 y PostgreSQL 17.11.
Ejecutar desde la raíz del proyecto en PowerShell, después de instalar el entorno
según el [README](../README.md#instalación). No se instala un servicio Windows.

## PostgreSQL local

Primero reproducir el entorno Python. Los siguientes pasos son de instalación
inicial: no repetir initdb ni la preparación sobre un clúster existente.
Desde PowerShell, en la raíz:

```powershell
New-Item -ItemType Directory -Path .local
$account = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
icacls .local /inheritance:r /grant:r "${account}:(OI)(CI)F" 'SYSTEM:(OI)(CI)F'
Invoke-WebRequest 'https://get.enterprisedb.com/postgresql/postgresql-17.11-3-windows-x64-binaries.zip' -OutFile .local/postgresql-binaries.zip
if ((Get-FileHash .local/postgresql-binaries.zip -Algorithm SHA256).Hash -ne '4B8DB0930C38F6EF845DB919551DEDDA3B6B845AEB0927B3D79A6E8E9E4537CF') { throw 'Archivo diferente al utilizado en E2' }
.\.venv\Scripts\python.exe -c "from pathlib import Path; from zipfile import ZipFile; root=Path('.local').resolve(); z=ZipFile(root/'postgresql-binaries.zip'); members=[m for m in z.namelist() if m.startswith(('pgsql/bin/','pgsql/lib/','pgsql/share/'))]; assert all((root/m).resolve().is_relative_to(root) for m in members); z.extractall(root,members); z.close()"
.\.venv\Scripts\python.exe -c "import secrets; from pathlib import Path; Path('.local/admin.password').open('x',encoding='utf-8').write(secrets.token_urlsafe(32))"
& .local/pgsql/bin/initdb.exe -D .local/pgdata -U postgres --encoding=UTF8 --locale=C --auth-host=scram-sha-256 --auth-local=scram-sha-256 --pwfile=.local/admin.password
& .local/pgsql/bin/pg_ctl.exe -D .local/pgdata -l .local/postgresql.log -o '-h 127.0.0.1 -p 55432' -w start
```

El SHA256 anterior identifica el archivo descargado y usado en E2; no se presenta
como una firma independiente del proveedor. Cada comando debe terminar sin error
antes de ejecutar el siguiente. Si el puerto está ocupado, no detener el proceso
ajeno: elegir otro puerto y mantenerlo consistente en la configuración.

Las ACL de .local restringen binarios, clúster y secretos al usuario actual y
SYSTEM. Las contraseñas aleatorias quedan exclusivamente en archivos privados
ignorados: admin.password y connections.json. No imprimirlos ni copiarlos a .env.example.

Continúe con [crear las bases](../README.md#crear-las-bases).

prepare crea únicamente dos bases nuevas y cuatro roles nuevos; no reemplaza nombres
existentes. schema aplica sql/001_schema.sql y los permisos en una transacción por
base; rechaza un modelo ya existente. Si una preparación parcial falla, conservar
los secretos y revisar el estado: no hay borrado, reinicio automático ni migraciones.
La herramienta solo acepta el clúster situado en .local/pgdata del proyecto.

Aplicar también las vistas de E7 y preparar la base sintética antes de ejecutar
las pruebas; los comandos completos figuran en el README. Los pasos de preparación
rechazan nombres y esquemas existentes: no son un mecanismo de reinicialización.

## Actualizar dependencias

Para la generación inicial, crear un entorno virtual y leer las herramientas
directamente de pyproject.toml. Este arranque resuelve transitivas; solo el lock
resultante y verificado se utiliza para reproducir instalaciones posteriores.

```powershell
$taskDeps = .\.venv\Scripts\python.exe -c "import tomllib; p=tomllib.load(open('pyproject.toml','rb')); print('\n'.join(p['project']['optional-dependencies']['dev']+p['build-system']['requires']))"
.\.venv\Scripts\python.exe -m pip --isolated install --index-url https://pypi.org/simple $taskDeps
$env:PIP_CONFIG_FILE = 'NUL'
$env:PIP_NO_INDEX = ''
$env:PIP_EXTRA_INDEX_URL = ''
$env:PIP_FIND_LINKS = ''
$env:PIP_TRUSTED_HOST = ''
$compileArgs = @('--index-url=https://pypi.org/simple', '--no-emit-index-url', '--no-emit-trusted-host', '--extra=dev', '--all-build-deps', '--strip-extras', '--allow-unsafe', '--generate-hashes', '--no-build-isolation', '--output-file=requirements.lock', 'pyproject.toml')
$env:CUSTOM_COMPILE_COMMAND = 'pip-compile ' + ($compileArgs -join ' ')
& .\.venv\Scripts\pip-compile.exe @compileArgs
```

En regeneraciones, conservar requirements.lock: el compilador reutiliza sus
versiones compatibles. Para una actualización deliberada, cambiar la declaración
correspondiente y añadir la opción de actualización del paquete afectado al
compilador. Revisar el cambio y verificar en un entorno limpio antes de aceptar
ambos archivos. No editar ni borrar el lock para actualizarlo. Si cambian pip o
pip-tools, preparar el generador con las nuevas versiones declaradas primero.
La cabecera se genera desde los argumentos reales: evita una discrepancia
observada al reconstruir opciones con pip-tools 7.6.1 y su Click transitivo.
Los ajustes de entorno del ejemplo afectan solo a la sesión de PowerShell actual.
