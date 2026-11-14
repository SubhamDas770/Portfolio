$ErrorActionPreference = "Stop"

$stateDirectory = Join-Path $env:LOCALAPPDATA "SubhamPortfolio\Terraform"
New-Item -ItemType Directory -Path $stateDirectory -Force | Out-Null
$statePath = Join-Path $stateDirectory "terraform.tfstate"

Push-Location $PSScriptRoot
try {
  terraform init -input=false -reconfigure -backend-config="path=$statePath"
  if ($LASTEXITCODE -ne 0) {
    throw "Terraform initialization failed."
  }
}
finally {
  Pop-Location
}
