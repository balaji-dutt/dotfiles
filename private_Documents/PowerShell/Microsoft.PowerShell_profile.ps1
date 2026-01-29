
# Load Claude Code Environment variables
$globalEnv = Join-Path $HOME ".config\powershell\claude-env.ps1"
if (Test-Path -LiteralPath $globalEnv) { . $globalEnv }

# Load OpenCode Environment variables
$globalEnv = Join-Path $HOME ".config\powershell\opencode-env.ps1"
if (Test-Path -LiteralPath $globalEnv) { . $globalEnv }

# Load Fix for programs that do not load Registry Path correctly (like VSCode)
$globalEnv = Join-Path $HOME ".config\powershell\repair-path.ps1"
if (Test-Path -LiteralPath $globalEnv) { . $globalEnv }
