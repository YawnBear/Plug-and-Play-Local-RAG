[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidatePattern('^[A-Za-z0-9_-]{32,128}$')]
    [string]$Code
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$form = [Windows.Forms.Form]::new()
$form.Text = 'Local RAG owner setup'
$form.StartPosition = 'CenterScreen'
$form.ClientSize = [Drawing.Size]::new(520, 255)
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.MinimizeBox = $false

$title = [Windows.Forms.Label]::new()
$title.Text = 'Create your Local RAG owner account'
$title.Font = [Drawing.Font]::new('Segoe UI', 14, [Drawing.FontStyle]::Bold)
$title.AutoSize = $true
$title.Location = [Drawing.Point]::new(24, 22)
$form.Controls.Add($title)

$description = [Windows.Forms.Label]::new()
$description.Text = 'Enter this one-time code on the setup page. It expires in 15 minutes.'
$description.AutoSize = $true
$description.Location = [Drawing.Point]::new(26, 62)
$form.Controls.Add($description)

$codeBox = [Windows.Forms.TextBox]::new()
$codeBox.Text = $Code
$codeBox.ReadOnly = $true
$codeBox.Font = [Drawing.Font]::new('Consolas', 11)
$codeBox.Location = [Drawing.Point]::new(28, 96)
$codeBox.Size = [Drawing.Size]::new(464, 32)
$codeBox.TabIndex = 0
$form.Controls.Add($codeBox)

$copy = [Windows.Forms.Button]::new()
$copy.Text = 'Copy code'
$copy.Location = [Drawing.Point]::new(28, 150)
$copy.Size = [Drawing.Size]::new(120, 38)
$copy.TabIndex = 1
$copy.Add_Click({
    [Windows.Forms.Clipboard]::SetText($Code)
    $copy.Text = 'Copied'
})
$form.Controls.Add($copy)

$done = [Windows.Forms.Button]::new()
$done.Text = 'Done'
$done.Location = [Drawing.Point]::new(372, 150)
$done.Size = [Drawing.Size]::new(120, 38)
$done.TabIndex = 2
$done.DialogResult = [Windows.Forms.DialogResult]::OK
$form.AcceptButton = $done
$form.Controls.Add($done)

$note = [Windows.Forms.Label]::new()
$note.Text = 'The code is copied only when you choose Copy code.'
$note.AutoSize = $true
$note.ForeColor = [Drawing.Color]::DimGray
$note.Location = [Drawing.Point]::new(28, 208)
$form.Controls.Add($note)

[void]$form.ShowDialog()
$form.Dispose()
