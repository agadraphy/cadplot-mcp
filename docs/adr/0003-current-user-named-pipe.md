# ADR 0003: Current-user-only named pipe

## Status

Accepted.

## Context

The Windows default named-pipe security descriptor can grant read access to Everyone and the
anonymous account. Product/status data is currently read-only, but future approved job messages
must not cross local user boundaries.

## Decision

- The .NET 8 host creates the pipe with `PipeOptions.CurrentUserOnly`.
- The .NET Framework 4.5 host creates a protected `PipeSecurity` ACL with inheritance disabled
  and full control granted only to the current Windows user SID.
- The bridge stays local and is never relayed through a public TCP listener.
- Named-pipe request size and positive command-whitelist controls remain in force.

Microsoft references:

- <https://learn.microsoft.com/en-us/windows/win32/ipc/named-pipe-security-and-access-rights>
- <https://learn.microsoft.com/en-us/dotnet/api/system.io.pipes.pipeoptions>

## Consequences

The MCP client and AutoCAD must run under the same Windows user. On .NET 8, elevation level is
also checked by `CurrentUserOnly`. Managed remote deployments require a separate authenticated
workstation bridge; loosening the local pipe ACL is not an accepted deployment shortcut.
