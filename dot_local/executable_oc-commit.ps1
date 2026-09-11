$ErrorActionPreference = 'Stop'

if ($args.Count -eq 1 -and $args[0] -in @('-h', '--help', 'help')) {
    @'
usage: oc-commit <git commit args>

Direct git commit replacement that records OpenCode as author and committer.
Pass normal git commit arguments directly; no wrapper-specific flags are needed.

Examples:
  oc-commit -m "subject"
  oc-commit -m "subject" -m "body"
  oc-commit -F C:\path\to\message
'@
    exit 0
}

$tool = 'opencode'
$stateName = 'ai-attestation-opencode.json'
$gitCommand = @(Get-Command git -CommandType Application -ErrorAction Stop)[0]
$strictUtf8 = [System.Text.UTF8Encoding]::new($false, $true)

function Resolve-AttestationStatePath {
    $info = [System.Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $gitCommand.Source
    $info.UseShellExecute = $false
    $info.WorkingDirectory = $PWD.ProviderPath
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.StandardOutputEncoding = $strictUtf8
    [void]$info.ArgumentList.Add('rev-parse')
    [void]$info.ArgumentList.Add('--git-path')
    [void]$info.ArgumentList.Add($stateName)
    [void]$info.Environment.Remove('AI_ATTESTATION_JSON')
    try {
        $child = [System.Diagnostics.Process]::Start($info)
        try {
            $stdout = $child.StandardOutput.ReadToEnd()
            $child.StandardError.ReadToEnd() | Out-Null
            $child.WaitForExit()
            if ($child.ExitCode -ne 0) {
                return $null
            }
            $candidate = $stdout.TrimEnd("`r", "`n")
            if ([string]::IsNullOrEmpty($candidate)) {
                return $null
            }
            if ([System.IO.Path]::IsPathRooted($candidate)) {
                return $candidate
            }
            return [System.IO.Path]::GetFullPath((Join-Path $PWD.ProviderPath $candidate))
        }
        finally {
            $child.Dispose()
        }
    }
    catch {
        return $null
    }
}

function Test-Identifier([string]$Value) {
    return $null -ne $Value -and $Value.Length -ge 1 -and $Value.Length -le 128 -and
        $Value -cmatch '\A[A-Za-z0-9][A-Za-z0-9._:/@+\-]*\z'
}

function Test-Role([string]$Value) {
    return $null -ne $Value -and $Value.Length -ge 1 -and $Value.Length -le 64 -and
        $Value -cmatch '\A[a-z0-9][a-z0-9-]*\z'
}

function Test-SourceDefinition([string]$Value) {
    return $null -ne $Value -and $Value.Length -ge 1 -and $Value.Length -le 512 -and
        $Value -cmatch '\A[A-Za-z0-9][A-Za-z0-9._@+\-]*(/[A-Za-z0-9][A-Za-z0-9._@+\-]*)*\z'
}

function Test-SourceDigest([string]$Value) {
    return $null -ne $Value -and $Value -cmatch '\Asha256:[0-9a-f]{64}\z'
}

function ConvertFrom-AttestationJson([string]$Payload) {
    try {
        $document = [System.Text.Json.JsonDocument]::Parse($Payload)
    }
    catch {
        return [pscustomobject]@{ Valid = $false; Records = @() }
    }
    try {
        $root = $document.RootElement
        if ($root.ValueKind -ne [System.Text.Json.JsonValueKind]::Object) {
            return [pscustomobject]@{ Valid = $false; Records = @() }
        }
        $rootProperties = @{}
        foreach ($property in $root.EnumerateObject()) {
            if ($property.Name -cnotin @('participants', 'schemaVersion') -or $rootProperties.ContainsKey($property.Name)) {
                return [pscustomobject]@{ Valid = $false; Records = @() }
            }
            $rootProperties[$property.Name] = $property.Value
        }
        if ($rootProperties.Count -ne 2 -or -not $rootProperties.ContainsKey('participants') -or
            -not $rootProperties.ContainsKey('schemaVersion')) {
            return [pscustomobject]@{ Valid = $false; Records = @() }
        }
        $version = 0
        if ($rootProperties['schemaVersion'].ValueKind -ne [System.Text.Json.JsonValueKind]::Number -or
            -not $rootProperties['schemaVersion'].TryGetInt32([ref]$version) -or $version -ne 1) {
            return [pscustomobject]@{ Valid = $false; Records = @() }
        }
        $participants = $rootProperties['participants']
        if ($participants.ValueKind -ne [System.Text.Json.JsonValueKind]::Array -or
            $participants.GetArrayLength() -lt 1 -or $participants.GetArrayLength() -gt 8) {
            return [pscustomobject]@{ Valid = $false; Records = @() }
        }

        $records = [System.Collections.Generic.List[object]]::new()
        $seen = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
        foreach ($participant in $participants.EnumerateArray()) {
            if ($participant.ValueKind -ne [System.Text.Json.JsonValueKind]::Object) {
                continue
            }
            $values = @{}
            foreach ($property in $participant.EnumerateObject()) {
                if ($property.Name -cnotin @('agent', 'model', 'role', 'sourceDefinition', 'sourceDigest', 'tool') -or
                    $values.ContainsKey($property.Name)) {
                    return [pscustomobject]@{ Valid = $false; Records = @() }
                }
                $values[$property.Name] = $property.Value
            }
            if (-not $values.ContainsKey('tool') -or
                $values['tool'].ValueKind -ne [System.Text.Json.JsonValueKind]::String) {
                continue
            }
            $recordTool = $values['tool'].GetString()
            if (-not (Test-Identifier $recordTool)) {
                continue
            }
            $recordAgent = ''
            $recordRole = ''
            $recordModel = ''
            $recordSource = ''
            $recordDigest = ''
            if ($values.ContainsKey('agent') -and $values['agent'].ValueKind -eq [System.Text.Json.JsonValueKind]::String) {
                $candidate = $values['agent'].GetString()
                if (Test-Identifier $candidate) { $recordAgent = $candidate }
            }
            if ($values.ContainsKey('role') -and $values['role'].ValueKind -eq [System.Text.Json.JsonValueKind]::String) {
                $candidate = $values['role'].GetString()
                if (Test-Role $candidate) { $recordRole = $candidate }
            }
            if ($values.ContainsKey('model') -and $values['model'].ValueKind -eq [System.Text.Json.JsonValueKind]::String) {
                $candidate = $values['model'].GetString()
                if (Test-Identifier $candidate) { $recordModel = $candidate }
            }
            if ($values.ContainsKey('sourceDefinition') -and $values.ContainsKey('sourceDigest') -and
                $values['sourceDefinition'].ValueKind -eq [System.Text.Json.JsonValueKind]::String -and
                $values['sourceDigest'].ValueKind -eq [System.Text.Json.JsonValueKind]::String) {
                $candidateSource = $values['sourceDefinition'].GetString()
                $candidateDigest = $values['sourceDigest'].GetString()
                if ((Test-SourceDefinition $candidateSource) -and (Test-SourceDigest $candidateDigest)) {
                    $recordSource = $candidateSource
                    $recordDigest = $candidateDigest
                }
            }
            $signature = @($recordTool, $recordAgent, $recordRole, $recordModel, $recordSource, $recordDigest) -join [char]31
            if ($seen.Add($signature)) {
                $records.Add([pscustomobject]@{
                    Tool = $recordTool
                    Agent = $recordAgent
                    Role = $recordRole
                    Model = $recordModel
                    SourceDefinition = $recordSource
                    SourceDigest = $recordDigest
                })
            }
        }
        return [pscustomobject]@{ Valid = $true; Records = $records.ToArray() }
    }
    finally {
        $document.Dispose()
    }
}

function ConvertFrom-AttestationWithJq([string]$Payload, $JqCommand) {
    $filter = @'
def identifier:
  type == "string" and length >= 1 and length <= 128 and
  test("\\A[A-Za-z0-9][A-Za-z0-9._:/@+-]*\\z");
def role:
  type == "string" and length >= 1 and length <= 64 and
  test("\\A[a-z0-9][a-z0-9-]*\\z");
def source_definition:
  type == "string" and length >= 1 and length <= 512 and
  test("\\A[A-Za-z0-9][A-Za-z0-9._@+-]*(/[A-Za-z0-9][A-Za-z0-9._@+-]*)*\\z");
def source_digest:
  type == "string" and test("\\Asha256:[0-9a-f]{64}\\z");
def optional_identifier($value): if ($value | identifier) then $value else "" end;
def optional_role($value): if ($value | role) then $value else "" end;
if type == "object" and has("schemaVersion") and has("participants") and
    ((keys_unsorted - ["participants", "schemaVersion"]) | length == 0) and
    .schemaVersion == 1 and (.participants | type == "array") and
    (.participants | length >= 1 and length <= 8) and
    all(.participants[];
      if type == "object" then
        ((keys_unsorted - ["agent", "model", "role", "sourceDefinition", "sourceDigest", "tool"]) | length == 0)
      else true end)
then
  [.participants[] |
    if type == "object" and (.tool | identifier) then
      (optional_identifier(.agent)) as $agent |
      (optional_role(.role)) as $role |
      (optional_identifier(.model)) as $model |
      (if (.sourceDefinition | source_definition) and (.sourceDigest | source_digest)
       then .sourceDefinition else "" end) as $sourceDefinition |
      (if $sourceDefinition != "" then .sourceDigest else "" end) as $sourceDigest |
      {Tool: .tool, Agent: $agent, Role: $role, Model: $model,
       SourceDefinition: $sourceDefinition, SourceDigest: $sourceDigest}
    else empty end] |
  reduce .[] as $record ([];
    if any(.[]; . == $record) then . else . + [$record] end)
else error("invalid handoff") end
'@
    $info = [System.Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $JqCommand.Source
    $info.UseShellExecute = $false
    $info.RedirectStandardInput = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.StandardInputEncoding = $strictUtf8
    $info.StandardOutputEncoding = $strictUtf8
    [void]$info.ArgumentList.Add('-c')
    [void]$info.ArgumentList.Add($filter)
    [void]$info.Environment.Remove('AI_ATTESTATION_JSON')
    try {
        $child = [System.Diagnostics.Process]::Start($info)
        try {
            $child.StandardInput.Write($Payload)
            $child.StandardInput.Close()
            $stdout = $child.StandardOutput.ReadToEnd()
            $child.StandardError.ReadToEnd() | Out-Null
            $child.WaitForExit()
            if ($child.ExitCode -ne 0) {
                return [pscustomobject]@{ Valid = $false; Records = @() }
            }
            $records = @(ConvertFrom-Json -InputObject $stdout -NoEnumerate -ErrorAction Stop)
            return [pscustomobject]@{ Valid = $true; Records = @($records[0]) }
        }
        finally {
            $child.Dispose()
        }
    }
    catch {
        return [pscustomobject]@{ Valid = $false; Records = @() }
    }
}

$statePath = Resolve-AttestationStatePath
$payload = $null
$environmentPayload = [System.Environment]::GetEnvironmentVariable('AI_ATTESTATION_JSON')
$environmentPresent = Test-Path Env:AI_ATTESTATION_JSON
if ($environmentPresent) {
    try {
        if ($strictUtf8.GetByteCount($environmentPayload) -le 16384) {
            $payload = $environmentPayload
        }
    }
    catch {}
}
elseif ($null -ne $statePath) {
    try {
        $file = [System.IO.FileInfo]::new($statePath)
        if ($file.Exists -and $file.Length -le 16384) {
            $payload = [System.IO.File]::ReadAllText($statePath, $strictUtf8)
        }
    }
    catch {}
}
if ($null -ne $statePath) {
    try { Remove-Item -LiteralPath $statePath -Force -ErrorAction Stop } catch {}
}

$parsed = [pscustomobject]@{ Valid = $false; Records = @() }
if (-not [string]::IsNullOrEmpty($payload)) {
    $jqCommand = @(Get-Command jq -CommandType Application -ErrorAction SilentlyContinue)[0]
    if ($null -ne $jqCommand) {
        $parsed = ConvertFrom-AttestationWithJq $payload $jqCommand
    }
    else {
        $parsed = ConvertFrom-AttestationJson $payload
    }
}
$records = @($parsed.Records)
if (-not $parsed.Valid -or $records.Count -eq 0) {
    $records = @([pscustomobject]@{
        Tool = $tool
        Agent = ''
        Role = ''
        Model = ''
        SourceDefinition = ''
        SourceDigest = ''
    })
}

$trailerArguments = [System.Collections.Generic.List[string]]::new()
foreach ($record in $records) {
    $participant = "tool=$($record.Tool)"
    if (-not [string]::IsNullOrEmpty($record.Agent)) { $participant += "; agent=$($record.Agent)" }
    if (-not [string]::IsNullOrEmpty($record.Role)) { $participant += "; role=$($record.Role)" }
    if (-not [string]::IsNullOrEmpty($record.Model)) { $participant += "; model=$($record.Model)" }
    $trailerArguments.Add('--trailer')
    $trailerArguments.Add("AI-Participant: $participant")
    if (-not [string]::IsNullOrEmpty($record.SourceDefinition)) {
        $trailerArguments.Add('--trailer')
        $trailerArguments.Add("Source-Definition: $($record.SourceDefinition)")
        $trailerArguments.Add('--trailer')
        $trailerArguments.Add("Source-Digest: $($record.SourceDigest)")
    }
}

$startInfo = [System.Diagnostics.ProcessStartInfo]::new()
$startInfo.FileName = $gitCommand.Source
$startInfo.UseShellExecute = $false
$startInfo.WorkingDirectory = $PWD.ProviderPath
foreach ($setting in @(
    'trailer.separators=:',
    'trailer.where=end',
    'trailer.ifexists=add',
    'trailer.ifmissing=add',
    'trailer.AI-Participant.key=AI-Participant',
    'trailer.AI-Participant.where=end',
    'trailer.AI-Participant.ifexists=add',
    'trailer.AI-Participant.ifmissing=add',
    'trailer.AI-Participant.cmd=printf %s',
    'trailer.Source-Definition.key=Source-Definition',
    'trailer.Source-Definition.where=end',
    'trailer.Source-Definition.ifexists=add',
    'trailer.Source-Definition.ifmissing=add',
    'trailer.Source-Definition.cmd=printf %s',
    'trailer.Source-Digest.key=Source-Digest',
    'trailer.Source-Digest.where=end',
    'trailer.Source-Digest.ifexists=add',
    'trailer.Source-Digest.ifmissing=add',
    'trailer.Source-Digest.cmd=printf %s'
)) {
    [void]$startInfo.ArgumentList.Add('-c')
    [void]$startInfo.ArgumentList.Add($setting)
}
[void]$startInfo.ArgumentList.Add('commit')
foreach ($argument in $trailerArguments) {
    [void]$startInfo.ArgumentList.Add($argument)
}
foreach ($argument in $args) {
    [void]$startInfo.ArgumentList.Add($argument)
}
[void]$startInfo.Environment.Remove('AI_ATTESTATION_JSON')
$startInfo.Environment['GIT_AUTHOR_NAME'] = 'OpenCode'
$startInfo.Environment['GIT_AUTHOR_EMAIL'] = 'noreply@opencode.ai'
$startInfo.Environment['GIT_COMMITTER_NAME'] = 'OpenCode'
$startInfo.Environment['GIT_COMMITTER_EMAIL'] = 'noreply@opencode.ai'

$process = [System.Diagnostics.Process]::Start($startInfo)
try {
    $process.WaitForExit()
    exit $process.ExitCode
}
finally {
    $process.Dispose()
}
