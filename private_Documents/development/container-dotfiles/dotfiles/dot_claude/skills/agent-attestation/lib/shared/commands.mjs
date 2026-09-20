const names = { bash: 'Bash', powershell: 'PowerShell' };

function direct(command, wrapper, shell) {
  if (typeof command !== 'string' || command.length > 65536) return undefined;
  if (command.includes('AI_ATTESTATION_JSON')) return undefined;
  const tokens = [];
  let token = '', quote = '', started = false;
  for (let i = 0; i < command.length; i++) {
    const char = command[i];
    if (quote) {
      if (char === quote) {
        if (shell === 'powershell' && quote === "'" && command[i + 1] === "'") {
          token += "'";
          i++;
        } else quote = '';
      } else {
        if (quote === '"' && /[$`]/.test(char)) return undefined;
        if (shell === 'bash' && quote === '"' && char === '\\') return undefined;
        token += char;
      }
      continue;
    }
    if (char === "'" || char === '"') { quote = char; started = true; continue; }
    if (shell === 'powershell' && char === '&' && tokens.length === 0 && !started) {
      tokens.push('&');
      continue;
    }
    if (/[\r\n;&|<>`$(){}#\\]/.test(char) || (shell === 'powershell' && char === '@')) return undefined;
    if (/\s/.test(char)) {
      if (started) { tokens.push(token); token = ''; started = false; }
    } else { token += char; started = true; }
  }
  if (quote) return undefined;
  if (started) tokens.push(token);
  if (shell === 'bash' && tokens[0] === 'command') tokens.shift();
  if (shell === 'powershell' && tokens[0] === '&') tokens.shift();
  const executable = tokens[0]?.split(shell === 'powershell' ? /[\\/]/ : /\//).at(-1);
  if (!executable || !tokens.length) return undefined;
  const expected = shell === 'powershell' ? [wrapper, `${wrapper}.ps1`] : [wrapper];
  if (!expected.includes(shell === 'powershell' ? executable.toLowerCase() : executable)) return undefined;
  return { command, shell };
}

function bashWords(command) {
  const words = [];
  let value = '', quote = '', start = -1;
  const finish = () => {
    if (start >= 0) words.push({ value, start });
    value = ''; start = -1;
  };
  for (let i = 0; i < command.length; i++) {
    const char = command[i];
    if (quote === "'") {
      if (char === quote) quote = '';
      else value += char;
      continue;
    }
    if (quote === '"') {
      if (char === quote) quote = '';
      else if (/[$`]/.test(char)) return undefined;
      else if (char === '\\' && /["\\$`\n]/.test(command[i + 1] || '')) {
        if (command[i + 1] === '\n') return undefined;
        value += command[++i];
      } else value += char;
      continue;
    }
    if (char === "'" || char === '"') {
      if (start < 0) start = i;
      quote = char;
    } else if (char === '&' && command[i + 1] === '&') {
      finish(); words.push({ value: '&&', start: i, operator: true }); i++;
    } else if (/[\r\n;&|<>`$(){}#\\*?\[\]~!]/.test(char)) return undefined;
    else if (/\s/.test(char)) finish();
    else { if (start < 0) start = i; value += char; }
  }
  if (quote) return undefined;
  finish();
  return words;
}

function bashPowerShell(command, wrapper) {
  const words = bashWords(command);
  if (!words?.length) return undefined;
  let index = 0;
  if (words[index].value === 'cd') {
    index++;
    if (words[index]?.value === '--') index++;
    const directory = words[index++];
    if (!directory?.value || directory.operator || directory.value.startsWith('-') ||
        !words[index]?.operator) return undefined;
    index++;
  }
  const executable = words[index++];
  if (!executable) return undefined;
  const separator = /^(?:[A-Za-z]:\\|\\\\)/.test(executable.value) ? /[\\/]/ : /\//;
  if (!['pwsh', 'pwsh.exe'].includes(executable.value.split(separator).at(-1).toLowerCase())) return undefined;
  const flags = new Set();
  while (['-noprofile', '-noninteractive', '-nologo'].includes(words[index]?.value.toLowerCase())) {
    const flag = words[index++].value.toLowerCase();
    if (flags.has(flag)) return undefined;
    flags.add(flag);
  }
  if (words[index++]?.value.toLowerCase() !== '-command') return undefined;
  const script = words[index++];
  if (!script || script.operator || index !== words.length || !direct(script.value, wrapper, 'powershell')) return undefined;
  return { command, shell: 'bash', offset: executable.start };
}

export function recognize(command, wrapper, shell = 'bash') {
  if (typeof command !== 'string' || command.length > 65536 || command.includes('AI_ATTESTATION_JSON')) return undefined;
  return direct(command, wrapper, shell) || (shell === 'bash' ? bashPowerShell(command, wrapper) : undefined);
}

export function inject(command, json, shell, invocation = {}) {
  if (shell === 'bash') {
    const offset = invocation.offset || 0;
    return `${command.slice(0, offset)}AI_ATTESTATION_JSON='${json.replaceAll("'", "'\\''")}' ${command.slice(offset)}`;
  }
  if (shell !== 'powershell') throw new Error('unsupported-shell');
  const value = json.replaceAll("'", "''");
  return `& { $attestationPresent = Test-Path Env:AI_ATTESTATION_JSON; $attestationSaved = $env:AI_ATTESTATION_JSON; try { $env:AI_ATTESTATION_JSON = '${value}'; ${command}; $attestationSuccess = $?; $attestationExit = if ($null -ne $LASTEXITCODE -and $LASTEXITCODE -ne 0) { $LASTEXITCODE } elseif ($attestationSuccess) { 0 } else { 1 } } finally { if ($attestationPresent) { $env:AI_ATTESTATION_JSON = $attestationSaved } else { Remove-Item Env:AI_ATTESTATION_JSON -ErrorAction SilentlyContinue } }; $global:LASTEXITCODE = $attestationExit }; exit $LASTEXITCODE`;
}

export function shellFor(tool) {
  return Object.keys(names).find(shell => names[shell] === tool);
}
