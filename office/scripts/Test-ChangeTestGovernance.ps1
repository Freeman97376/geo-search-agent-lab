[CmdletBinding()]
param(
    [Parameter()]
    [string]$ProjectRoot = (Get-Location).Path,

    [Parameter()]
    [switch]$Quiet,

    [Parameter()]
    [switch]$NoExit
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$resolvedRoot = [System.IO.Path]::GetFullPath($ProjectRoot).TrimEnd([System.IO.Path]::DirectorySeparatorChar)
$failures = [System.Collections.Generic.List[string]]::new()
$caseById = @{}

function Add-Failure {
    param([Parameter(Mandatory)][string]$Message)
    $script:failures.Add($Message)
}

function Get-Field {
    param(
        [Parameter(Mandatory)][string]$Content,
        [Parameter(Mandatory)][string]$Key,
        [Parameter(Mandatory)][string]$Record
    )
    $pattern = '(?m)^' + [regex]::Escape($Key) + ':\s*(?<value>.+?)\s*$'
    $match = [regex]::Match($Content, $pattern)
    if (-not $match.Success) {
        Add-Failure "$Record is missing frontmatter field: $Key"
        return $null
    }
    return $match.Groups['value'].Value.Trim().Trim('"', "'")
}

function Test-MeaningfulSection {
    param(
        [Parameter(Mandatory)][string]$Content,
        [Parameter(Mandatory)][string]$Heading
    )
    $pattern = '(?ms)^##\s+' + [regex]::Escape($Heading) + '\s*\r?\n(?<body>.*?)(?=^##\s+|\z)'
    $match = [regex]::Match($Content, $pattern)
    if (-not $match.Success) {
        return $false
    }
    $body = $match.Groups['body'].Value.Trim()
    return $body.Length -gt 0 -and $body -notmatch '^\[?TODO' -and $body -notmatch '^<.+>$'
}

function Test-RelativeFile {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Record,
        [Parameter(Mandatory)][string]$Field
    )
    if ($Path -eq 'none') {
        return $false
    }
    if ([System.IO.Path]::IsPathRooted($Path)) {
        Add-Failure "$Record field $Field must use a project-relative path: $Path"
        return $false
    }
    $fullPath = Join-Path $resolvedRoot $Path
    if (-not (Test-Path -LiteralPath $fullPath -PathType Leaf)) {
        Add-Failure "$Record points to a missing file in ${Field}: $Path"
        return $false
    }
    return $true
}

$requiredFiles = @(
    'office\records\test-ledger.md',
    'office\templates\test-ledger.template.md',
    'office\templates\test-case.template.md',
    'office\templates\known-error.template.md'
)
foreach ($relativePath in $requiredFiles) {
    if (-not (Test-Path -LiteralPath (Join-Path $resolvedRoot $relativePath) -PathType Leaf)) {
        Add-Failure "Missing test-governance baseline file: $relativePath"
    }
}

$ledger = Join-Path $resolvedRoot 'office\records\test-ledger.md'
if (Test-Path -LiteralPath $ledger -PathType Leaf) {
    $ledgerContent = Get-Content -LiteralPath $ledger -Raw
    foreach ($heading in @('## Canonical test cases', '## Known errors', '## Waivers')) {
        if (-not $ledgerContent.Contains($heading)) {
            Add-Failure "Test ledger is missing heading: $heading"
        }
    }
}

$caseDirectory = Join-Path $resolvedRoot 'office\records\test-cases'
$caseRecords = @()
if (Test-Path -LiteralPath $caseDirectory -PathType Container) {
    $caseRecords = @(Get-ChildItem -LiteralPath $caseDirectory -Filter '*.md' -File -Recurse)
}

$requiredCaseFields = @(
    'test_case_version', 'id', 'type', 'spec_status', 'execution_status', 'version',
    'supersedes', 'superseded_by', 'purpose_status', 'process_status',
    'expected_result_status', 'confirmed_at', 'confirmed_by', 'source_kind',
    'source_ids', 'test_path', 'test_command', 'last_verified_at', 'verified_against',
    'coverage_paths', 'contract_ids'
)
$allowedSpecStates = @('draft', 'derived', 'confirmed', 'superseded')
$allowedExecutionStates = @('not_implemented', 'not_run', 'passed', 'failed', 'blocked', 'stale')

foreach ($file in $caseRecords) {
    $relative = $file.FullName.Substring($resolvedRoot.Length).TrimStart([char[]]@('\', '/'))
    $content = Get-Content -LiteralPath $file.FullName -Raw
    $fields = @{}
    foreach ($key in $requiredCaseFields) {
        $fields[$key] = Get-Field -Content $content -Key $key -Record $relative
    }
    if ($null -eq $fields['id']) {
        continue
    }
    $id = $fields['id']
    if ($caseById.ContainsKey($id)) {
        Add-Failure "Duplicate test-case ID: $id"
    } else {
        $caseById[$id] = [pscustomobject]@{ Fields = $fields; Relative = $relative; Content = $content }
    }

    if ($null -ne $fields['spec_status'] -and $allowedSpecStates -notcontains $fields['spec_status']) {
        Add-Failure "$relative has invalid spec_status: $($fields['spec_status'])"
    }
    if ($null -ne $fields['execution_status'] -and $allowedExecutionStates -notcontains $fields['execution_status']) {
        Add-Failure "$relative has invalid execution_status: $($fields['execution_status'])"
    }
    foreach ($heading in @('Purpose', 'Preconditions', 'Process', 'Expected result', 'Actual result', 'Evidence', 'Confirmation', 'Notes')) {
        if (-not (Test-MeaningfulSection -Content $content -Heading $heading)) {
            Add-Failure "$relative is missing meaningful section: $heading"
        }
    }

    if ($fields['spec_status'] -eq 'confirmed') {
        foreach ($gate in @('purpose_status', 'process_status', 'expected_result_status')) {
            if ($fields[$gate] -ne 'confirmed') {
                Add-Failure "$relative cannot be confirmed while $gate is $($fields[$gate])"
            }
        }
        if ($fields['confirmed_at'] -eq 'none' -or $fields['confirmed_by'] -ne 'user') {
            Add-Failure "$relative confirmed cases require confirmed_at and confirmed_by: user"
        }
    }
    if ($fields['spec_status'] -eq 'superseded' -and $fields['superseded_by'] -eq 'none') {
        Add-Failure "$relative superseded cases require superseded_by"
    }
    $versionNumber = 0
    if (-not [int]::TryParse($fields['version'], [ref]$versionNumber) -or $versionNumber -lt 1) {
        Add-Failure "$relative has invalid version: $($fields['version'])"
    } elseif ($versionNumber -gt 1 -and $fields['supersedes'] -eq 'none') {
        Add-Failure "$relative version $versionNumber requires supersedes"
    }

    if ($fields['execution_status'] -eq 'passed') {
        [void](Test-RelativeFile -Path $fields['test_path'] -Record $relative -Field 'test_path')
        foreach ($key in @('test_command', 'last_verified_at', 'verified_against')) {
            if ($fields[$key] -eq 'none') {
                Add-Failure "$relative passed cases require $key"
            }
        }
    }
}

foreach ($id in @($caseById.Keys)) {
    $record = $caseById[$id]
    $supersedes = $record.Fields['supersedes']
    $supersededBy = $record.Fields['superseded_by']
    if ($supersedes -ne 'none') {
        if (-not $caseById.ContainsKey($supersedes)) {
            Add-Failure "$($record.Relative) supersedes missing case: $supersedes"
        } elseif ($caseById[$supersedes].Fields['superseded_by'] -ne $id) {
            Add-Failure "$($record.Relative) and $supersedes do not have reciprocal supersession links"
        }
    }
    if ($record.Fields['spec_status'] -eq 'superseded' -and $supersededBy -ne 'none') {
        if (-not $caseById.ContainsKey($supersededBy)) {
            Add-Failure "$($record.Relative) points to missing superseding case: $supersededBy"
        } elseif ($caseById[$supersededBy].Fields['supersedes'] -ne $id) {
            Add-Failure "$($record.Relative) and $supersededBy do not have reciprocal supersession links"
        }
    }
}

$errorDirectory = Join-Path $resolvedRoot 'office\records\known-errors'
$errorRecords = @()
if (Test-Path -LiteralPath $errorDirectory -PathType Container) {
    $errorRecords = @(Get-ChildItem -LiteralPath $errorDirectory -Filter '*.md' -File)
}
$requiredErrorFields = @(
    'known_error_version', 'id', 'status', 'severity', 'reported_at', 'resolved_at',
    'root_cause_status', 'regression_test_case', 'regression_test_path',
    'regression_test_command', 'last_verified_at', 'verified_against', 'waiver_status',
    'waiver_confirmed_at', 'superseded_by'
)
$allowedErrorStates = @('open', 'reproduced', 'regression_draft', 'fixed_unverified', 'verified', 'superseded')
$errorIds = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)

foreach ($file in $errorRecords) {
    $relative = $file.FullName.Substring($resolvedRoot.Length).TrimStart([char[]]@('\', '/'))
    $content = Get-Content -LiteralPath $file.FullName -Raw
    $fields = @{}
    foreach ($key in $requiredErrorFields) {
        $fields[$key] = Get-Field -Content $content -Key $key -Record $relative
    }
    if ($null -eq $fields['id']) {
        continue
    }
    if (-not $errorIds.Add($fields['id'])) {
        Add-Failure "Duplicate known-error ID: $($fields['id'])"
    }
    if ($null -ne $fields['status'] -and $allowedErrorStates -notcontains $fields['status']) {
        Add-Failure "$relative has invalid known-error status: $($fields['status'])"
    }
    foreach ($heading in @('Symptom', 'Trigger', 'Evidence', 'Expected behavior', 'Root cause', 'Resolution', 'Verification', 'Manual verification', 'Notes')) {
        if (-not (Test-MeaningfulSection -Content $content -Heading $heading)) {
            Add-Failure "$relative is missing meaningful section: $heading"
        }
    }

    if ($fields['status'] -eq 'verified') {
        $waived = $fields['waiver_status'] -eq 'user-approved'
        if ($waived) {
            if ($fields['waiver_confirmed_at'] -eq 'none') {
                Add-Failure "$relative approved manual waivers require waiver_confirmed_at"
            }
            $manualPattern = '(?ms)^##\s+Manual verification\s*\r?\n(?<body>.*?)(?=^##\s+|\z)'
            $manualMatch = [regex]::Match($content, $manualPattern)
            if (-not $manualMatch.Success -or $manualMatch.Groups['body'].Value.Trim() -match '^Not applicable\.?$') {
                Add-Failure "$relative approved manual waivers require substantive Manual verification steps"
            }
        } else {
            foreach ($key in @('regression_test_case', 'regression_test_path', 'regression_test_command', 'last_verified_at', 'verified_against')) {
                if ($fields[$key] -eq 'none') {
                    Add-Failure "$relative verified errors require $key or a user-approved waiver"
                }
            }
            if ($fields['regression_test_path'] -ne 'none') {
                [void](Test-RelativeFile -Path $fields['regression_test_path'] -Record $relative -Field 'regression_test_path')
            }
            $caseId = $fields['regression_test_case']
            if ($caseId -ne 'none') {
                if (-not $caseById.ContainsKey($caseId)) {
                    Add-Failure "$relative references missing regression test case: $caseId"
                } elseif ($caseById[$caseId].Fields['execution_status'] -ne 'passed') {
                    Add-Failure "$relative cannot be verified while regression case $caseId is $($caseById[$caseId].Fields['execution_status'])"
                }
            }
        }
    }
    if ($fields['status'] -eq 'superseded' -and $fields['superseded_by'] -eq 'none') {
        Add-Failure "$relative superseded errors require superseded_by"
    }
}

$valid = $failures.Count -eq 0
if (-not $Quiet) {
    if ($valid) {
        Write-Output "[PASS] Change-test governance is valid: $resolvedRoot"
    } else {
        foreach ($failure in $failures) {
            Write-Output "[FAIL] $failure"
        }
        Write-Output "[FAIL] Change-test governance has $($failures.Count) error(s)."
    }
}

if ($NoExit) {
    return $valid
}
if ($valid) {
    exit 0
}
exit 1
