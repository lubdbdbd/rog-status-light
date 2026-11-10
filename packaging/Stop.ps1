$ErrorActionPreference = 'Stop'
if (-not (Get-Process -Name ROGStatusLight -ErrorAction SilentlyContinue)) { return }
if (-not ('ROGStatusLight.NativeStop' -as [type])) {
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
namespace ROGStatusLight {
    public static class NativeStop {
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode)]
        public static extern IntPtr OpenEvent(uint access, bool inherit, string name);
        [DllImport("kernel32.dll")]
        public static extern bool SetEvent(IntPtr handle);
        [DllImport("kernel32.dll")]
        public static extern bool CloseHandle(IntPtr handle);
    }
}
"@
}
$signal = [ROGStatusLight.NativeStop]::OpenEvent(2, $false, 'Local\ROGStatusLight-v1-quit')
if ($signal -ne [IntPtr]::Zero) {
    try { [void][ROGStatusLight.NativeStop]::SetEvent($signal) }
    finally { [void][ROGStatusLight.NativeStop]::CloseHandle($signal) }
} else {
    $data = Join-Path $env:USERPROFILE '.rog-status-light'
    if (Test-Path -LiteralPath $data) {
        New-Item -ItemType File -Path (Join-Path $data 'stop-request') -Force | Out-Null
    }
}
