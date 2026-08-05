[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
[pscustomobject]@{
    schema_version = 1
    changes_applied = $false
    keys_generated = $false
    implementation = 'Install-RagCertificates.ps1'
    openssl = 'exact administrator-supplied local path and SHA-256 required'
    canonical_host = 'rag.home.arpa'
    lifecycle = @(
        'Generate the private CA locally during an attended action',
        'Issue DNS SAN rag.home.arpa leaf',
        'Issue loopback API server and Caddy client mTLS leaves',
        'Restrict each private key to its service identity',
        'Install CA trust only after an explicit administrator prompt',
        'Validate replacement leaf before atomic switch and Caddy reload',
        'Keep the prior valid leaf for rollback'
    )
    http_fallback = $false
} | ConvertTo-Json -Depth 5
