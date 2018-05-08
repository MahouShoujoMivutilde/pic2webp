$v = ".\venv"
$exe = '.\dist\pic2webp.exe'

function activate() {
    &$v\Scripts\Activate.ps1
}

if(!(Test-Path -Path $v)){
    Write-Host "виртуальное окружение отсутствует, создаем..." -Foregroundcolor Cyan
    python -m venv $v
    activate
    Write-Host "устанавливаем зависимости..." -Foregroundcolor Cyan
    pip install -r .\requirements.txt
    deactivate
}

activate
Write-Host "собираем exe..." -Foregroundcolor Green
pyinstaller .\pic2webp.spec --clean --log-level WARN
deactivate

if(Test-Path -Path $exe) {
    Write-Host "успех!" -Foregroundcolor Green
}