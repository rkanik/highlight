param(
  [Parameter(Mandatory = $true)][string]$Dir,
  [string]$OutJson = "",
  [int]$EveryN = 1,
  [int]$MaxFiles = 0
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null

$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
  })[0]

Function Await($WinRtTask, $ResultType) {
  $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
  $netTask = $asTask.Invoke($null, @($WinRtTask))
  $netTask.Wait(-1) | Out-Null
  $netTask.Result
}

[Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null

$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($null -eq $engine) { throw 'OCR engine unavailable' }

$files = Get-ChildItem -LiteralPath $Dir -Filter '*.jpg' | Sort-Object Name
if ($MaxFiles -gt 0) { $files = $files | Select-Object -First $MaxFiles }

$results = New-Object System.Collections.Generic.List[object]
$i = 0
foreach ($f in $files) {
  if (($i % $EveryN) -ne 0) { $i++; continue }
  try {
    $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($f.FullName)) ([Windows.Storage.StorageFile])
    $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $ocr = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
    $stream.Dispose()
    $text = $ocr.Text
  }
  catch {
    $text = ""
  }
  # filename f_000123.jpg => index 122 => seconds if 1fps
  $m = [regex]::Match($f.BaseName, '(\d+)$')
  $idx = if ($m.Success) { [int]$m.Groups[1].Value - 1 } else { $i }
  if ($text -and $text.Trim().Length -gt 0) {
    $results.Add([pscustomobject]@{ t = $idx; file = $f.Name; text = $text })
  }
  $i++
  if (($i % 100) -eq 0) { Write-Host "ocr $i / $($files.Count)" }
}

if (-not $OutJson) {
  $OutJson = Join-Path $Dir 'ocr.json'
}
$results | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $OutJson -Encoding UTF8
Write-Host "wrote $($results.Count) texts -> $OutJson"
