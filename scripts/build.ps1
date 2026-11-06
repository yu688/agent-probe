param(
    [string]$PicoRoot = 'C:\Users\Administrator\.pico-sdk',
    [string]$SdkVersion = '2.3.1',
    [string]$BuildDir = 'build/rp2350',
    [switch]$EnableUartB
)
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$SourceDir = Join-Path $ProjectRoot 'firmware/rp2350'
$SdkPath = Join-Path $PicoRoot "sdk\$SdkVersion"
$CmakeExe = Join-Path $PicoRoot 'cmake\v4.3.4\bin\cmake.exe'
$NinjaExe = Join-Path $PicoRoot 'ninja\v1.13.2\ninja.exe'
$ToolchainPath = Join-Path $PicoRoot 'toolchain\15_2_Rel1'
$PicotoolPath = Join-Path $PicoRoot "picotool\$SdkVersion\picotool"
$PioasmPath = Join-Path $PicoRoot "tools\$SdkVersion\pioasm"
foreach($RequiredPath in @($SdkPath, $CmakeExe, $NinjaExe, $ToolchainPath, $PicotoolPath, $PioasmPath))
{
    if(-not (Test-Path -LiteralPath $RequiredPath))
    {
        throw "缺少构建依赖：$RequiredPath"
    }
}
if(-not [IO.Path]::IsPathRooted($BuildDir))
{
    $BuildDir = Join-Path $ProjectRoot $BuildDir
}
$UartB = if($EnableUartB) { 'ON' } else { 'OFF' }
& $CmakeExe -S $SourceDir -B $BuildDir -G Ninja `
    "-DCMAKE_MAKE_PROGRAM=$NinjaExe" "-DPICO_SDK_PATH=$SdkPath" `
    "-DPICO_TOOLCHAIN_PATH=$ToolchainPath" "-Dpicotool_DIR=$PicotoolPath" `
    "-Dpioasm_DIR=$PioasmPath" `
    '-DCMAKE_BUILD_TYPE=Release' "-DAGENT_ENABLE_UART_B=$UartB"
if($LASTEXITCODE -ne 0)
{
    throw 'CMake 配置失败'
}
& $CmakeExe --build $BuildDir --parallel
if($LASTEXITCODE -ne 0)
{
    throw '编译失败'
}
Write-Host "构建完成：$(Join-Path $BuildDir 'agent_probe.uf2')"
