
# Load Claude Code Environment variables
$globalEnv = Join-Path $HOME ".config\powershell\claude-env.ps1"
if (Test-Path -LiteralPath $globalEnv) { . $globalEnv }

# Load OpenCode Environment variables
$globalEnv = Join-Path $HOME ".config\powershell\opencode-env.ps1"
if (Test-Path -LiteralPath $globalEnv) { . $globalEnv }

# Load Fix for programs that do not load Registry Path correctly (like VSCode)
$globalEnv = Join-Path $HOME ".config\powershell\repair-path.ps1"
if (Test-Path -LiteralPath $globalEnv) { . $globalEnv }

# Load PowerShell aliases
$globalEnv = Join-Path $HOME ".config\powershell\aliases.ps1"
if (Test-Path -LiteralPath $globalEnv) { . $globalEnv }

# Load git helpers
$globalEnv = Join-Path $HOME ".config\powershell\git.ps1"
if (Test-Path -LiteralPath $globalEnv) { . $globalEnv }

# Load package managers
$globalEnv = Join-Path $HOME ".config\powershell\packages.ps1"
if (Test-Path -LiteralPath $globalEnv) { . $globalEnv }

# Load prompt configuration
$globalEnv = Join-Path $HOME ".config\powershell\prompt.ps1"
if (Test-Path -LiteralPath $globalEnv) { . $globalEnv }
