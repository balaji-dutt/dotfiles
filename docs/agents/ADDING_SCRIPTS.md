<!-- markdownlint-disable MD007 MD013 MD023 MD031 MD032 MD034 MD040 MD041 MD051 -->
<!-- markdownlint-configure-file
{
  options": {
    "frontMatter": "(^---\\s*$[^]*?^---\\s*$)(\\r\\n|\\r|\\n|$)"
  },
  "no-trailing-spaces": false,
  "no-hard-tabs": true
}
-->

# Standards

- Use existing code style conventions and patterns.
- Scripts must always be created in the `.chezmoiscripts` folder and nowhere else.
- Scripts must always use `chezmoi` templates and have logic that makes the execution logic conditional on which operating system it is running under.
