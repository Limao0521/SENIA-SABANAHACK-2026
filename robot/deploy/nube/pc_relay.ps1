<#
.SYNOPSIS
    Relé del gateway en la nube para el robot X2 (modo A: robot sin internet).

.DESCRIPTION
    El robot (10.0.1.41) solo tiene cable Ethernet hasta el PC de desarrollo
    (10.0.1.50). Este PC mantiene el túnel WireGuard hacia el VPS (10.66.0.1).
    El script crea un portproxy (netsh) para que el robot alcance el gateway en
    la nube como http://10.0.1.50:8443, y una regla de cortafuegos que limita
    el acceso entrante al propio robot.

    Requisitos: ejecutar como Administrador y tener el túnel WireGuard activo
    en el PC (deploy/nube/wireguard/generar_wg.sh genera wg-cliente.conf).

.PARAMETER Quitar
    Elimina el portproxy y la regla de cortafuegos.

.PARAMETER Estado
    Muestra los portproxy configurados (netsh interface portproxy show all).

.EXAMPLE
    deploy\nube\pc_relay.ps1                 # crear relé (10.0.1.50:8443 -> 10.66.0.1:8443)
    deploy\nube\pc_relay.ps1 -Estado         # ver portproxy activos
    deploy\nube\pc_relay.ps1 -Quitar         # deshacer
#>
[CmdletBinding()]
param(
    [string]$ListenAddress = "10.0.1.50",   # IP del PC en la red cableada del robot
    [string]$CloudAddress  = "10.66.0.1",   # VPS a través del túnel WireGuard
    [int]$Port             = 8443,          # puerto del gateway (NUBE_PUERTO)
    [string]$RobotAddress  = "10.0.1.41",   # único origen permitido en el cortafuegos
    [switch]$Quitar,
    [switch]$Estado
)

$ErrorActionPreference = "Stop"
$nombreRegla = "X2 nube relay"

# --- Comprobación de privilegios de Administrador -----------------------------
$identidad  = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal  = New-Object Security.Principal.WindowsPrincipal($identidad)
$esAdmin    = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $esAdmin) {
    Write-Host "ERROR: este script debe ejecutarse como Administrador." -ForegroundColor Red
    Write-Host "Ábralo con: clic derecho en PowerShell -> 'Ejecutar como administrador'."
    exit 1
}

# --- Modo -Estado: solo mostrar los portproxy actuales ------------------------
if ($Estado) {
    Write-Host "Portproxy configurados en el PC:"
    netsh interface portproxy show all
    exit 0
}

# --- Modo -Quitar: eliminar portproxy y regla ---------------------------------
if ($Quitar) {
    Write-Host "Eliminando el relé $ListenAddress`:$Port ..."
    netsh interface portproxy delete v4tov4 listenaddress=$ListenAddress listenport=$Port
    $regla = Get-NetFirewallRule -DisplayName $nombreRegla -ErrorAction SilentlyContinue
    if ($regla) {
        Remove-NetFirewallRule -DisplayName $nombreRegla
        Write-Host "Regla de cortafuegos '$nombreRegla' eliminada."
    } else {
        Write-Host "La regla de cortafuegos '$nombreRegla' no existía."
    }
    Write-Host "Relé eliminado. El robot vuelve a depender solo de la pila a bordo."
    exit 0
}

# --- Modo por defecto: crear/actualizar el relé -------------------------------
# El portproxy lo gestiona el servicio IP Helper; si está detenido, se arranca.
$servicio = Get-Service -Name "iphlpsvc" -ErrorAction SilentlyContinue
if ($servicio -and ($servicio.Status -ne "Running")) {
    Write-Host "Arrancando el servicio IP Helper (iphlpsvc), necesario para portproxy..."
    Start-Service -Name "iphlpsvc"
}

Write-Host "Creando portproxy: $ListenAddress`:$Port -> $CloudAddress`:$Port (TCP v4)"
netsh interface portproxy add v4tov4 listenaddress=$ListenAddress listenport=$Port connectaddress=$CloudAddress connectport=$Port

# Regla de cortafuegos: solo el robot puede usar el relé (se recrea para que los
# parámetros reflejen siempre los argumentos actuales; evita duplicados).
$regla = Get-NetFirewallRule -DisplayName $nombreRegla -ErrorAction SilentlyContinue
if ($regla) {
    Remove-NetFirewallRule -DisplayName $nombreRegla
}
New-NetFirewallRule -DisplayName $nombreRegla -Direction Inbound -LocalAddress $ListenAddress -RemoteAddress $RobotAddress -Protocol TCP -LocalPort $Port -Action Allow | Out-Null
Write-Host "Regla de cortafuegos '$nombreRegla' activa (origen permitido: $RobotAddress)."

# --- Recordatorios finales ----------------------------------------------------
Write-Host ""
Write-Host "Relé listo. Para usarlo:"
Write-Host "  1. El túnel WireGuard del PC debe estar activo (wg-cliente.conf importado"
Write-Host "     en WireGuard for Windows); compruebe con: Test-NetConnection $CloudAddress -Port $Port"
Write-Host "  2. En el robot, exporte:"
Write-Host "       HRI_NUBE_URL=http://${ListenAddress}:${Port}"
Write-Host "       HRI_NUBE_TOKEN=<contenido de deploy/nube/.secretos/token>"
Write-Host "  3. Verifique desde el robot: python3 -m x2_ultra.hri_runtime.nube"
exit 0
