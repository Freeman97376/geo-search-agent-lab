[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$validator = Join-Path $projectRoot 'office\scripts\Test-ChangeTestGovernance.ps1'
$powershellExe = Join-Path $PSHOME 'powershell.exe'
if (-not (Test-Path -LiteralPath $powershellExe -PathType Leaf)) {
    $powershellExe = (Get-Command powershell.exe -ErrorAction Stop).Source
}

$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("change-test-governance-tests-{0}" -f [guid]::NewGuid().ToString('N'))
$failures = [System.Collections.Generic.List[string]]::new()

function Invoke-Validator {
    param([Parameter(Mandatory)][string]$Root)
    & $powershellExe -NoProfile -ExecutionPolicy Bypass -File $validator -ProjectRoot $Root *> $null
    return $LASTEXITCODE
}

function Assert-True {
    param(
        [Parameter(Mandatory)][bool]$Condition,
        [Parameter(Mandatory)][string]$Name
    )
    if ($Condition) {
        Write-Output "[PASS] $Name"
    } else {
        $script:failures.Add($Name)
        Write-Output "[FAIL] $Name"
    }
}

try {
    New-Item -ItemType Directory -Path $testRoot | Out-Null

    $baselineExit = Invoke-Validator -Root $projectRoot
    Assert-True -Condition ($baselineExit -eq 0) -Name 'Current change-test governance baseline validates'

    $targetRoot = Join-Path $testRoot 'fixture-project'
    New-Item -ItemType Directory -Path $targetRoot | Out-Null
    Copy-Item -LiteralPath (Join-Path $projectRoot 'office') -Destination (Join-Path $targetRoot 'office') -Recurse
    New-Item -ItemType Directory -Path (Join-Path $targetRoot 'tests') | Out-Null
    Set-Content -LiteralPath (Join-Path $targetRoot 'tests\example.tests.ps1') -Value "Describe 'example' { It 'passes' { `$true | Should -BeTrue } }" -Encoding UTF8

    $caseDirectory = Join-Path $targetRoot 'office\records\test-cases\functional'
    New-Item -ItemType Directory -Path $caseDirectory -Force | Out-Null
    $casePath = Join-Path $caseDirectory 'tc-example-001-v1.md'
    $caseContent = @'
---
test_case_version: 1
id: TC-EXAMPLE-001-v1
type: functional
spec_status: confirmed
execution_status: passed
version: 1
supersedes: none
superseded_by: none
purpose_status: confirmed
process_status: confirmed
expected_result_status: confirmed
confirmed_at: 2026-08-28T12:00:00-07:00
confirmed_by: user
source_kind: user-confirmed
source_ids: CHANGE-001
test_path: tests\example.tests.ps1
test_command: powershell -File tests\example.tests.ps1
last_verified_at: 2026-08-28T12:10:00-07:00
verified_against: fixture-state-001
coverage_paths: src\example.ps1
contract_ids: CONTRACT-001
---

# TC-EXAMPLE-001-v1 - Example behavior

## Purpose

Protect the confirmed example behavior.

## Preconditions

Use the isolated fixture project.

## Process

Run the example test command.

## Expected result

The command exits successfully.

## Actual result

The command passed in the fixture state.

## Evidence

Executed the recorded command at the recorded state.

## Confirmation

The user confirmed purpose, process, and expected result.

## Notes

No additional constraints.
'@
    Set-Content -LiteralPath $casePath -Value $caseContent -Encoding UTF8

    $validCaseExit = Invoke-Validator -Root $targetRoot
    Assert-True -Condition ($validCaseExit -eq 0) -Name 'Confirmed passed case with evidence validates'

    $errorDirectory = Join-Path $targetRoot 'office\records\known-errors'
    New-Item -ItemType Directory -Path $errorDirectory -Force | Out-Null
    $errorPath = Join-Path $errorDirectory 'err-example-001.md'
    $errorContent = @'
---
known_error_version: 1
id: ERR-EXAMPLE-001
status: verified
severity: medium
reported_at: 2026-08-28T11:00:00-07:00
resolved_at: 2026-08-28T12:10:00-07:00
root_cause_status: confirmed
regression_test_case: TC-EXAMPLE-001-v1
regression_test_path: tests\example.tests.ps1
regression_test_command: powershell -File tests\example.tests.ps1
last_verified_at: 2026-08-28T12:10:00-07:00
verified_against: fixture-state-001
waiver_status: none
waiver_confirmed_at: none
superseded_by: none
---

# ERR-EXAMPLE-001 - Example failure

## Symptom

The example behavior previously failed.

## Trigger

Run the example action in the fixture state.

## Evidence

The failure was reproduced before the fixture fix.

## Expected behavior

The example action completes successfully.

## Root cause

The fixture implementation returned the wrong state.

## Resolution

The fixture implementation was corrected.

## Verification

TC-EXAMPLE-001-v1 passed with the recorded command.

## Manual verification

Not applicable.

## Notes

The regression case is the verification source.
'@
    Set-Content -LiteralPath $errorPath -Value $errorContent -Encoding UTF8

    $verifiedErrorExit = Invoke-Validator -Root $targetRoot
    Assert-True -Condition ($verifiedErrorExit -eq 0) -Name 'Verified error with passed regression case validates'

    $staleContent = $caseContent.Replace('execution_status: passed', 'execution_status: stale')
    Set-Content -LiteralPath $casePath -Value $staleContent -Encoding UTF8
    $staleErrorExit = Invoke-Validator -Root $targetRoot
    Assert-True -Condition ($staleErrorExit -ne 0) -Name 'Verified error is rejected when its regression case is stale'
    Set-Content -LiteralPath $casePath -Value $caseContent -Encoding UTF8

    $invalidConfirmation = $caseContent.Replace('purpose_status: confirmed', 'purpose_status: draft')
    Set-Content -LiteralPath $casePath -Value $invalidConfirmation -Encoding UTF8
    $invalidConfirmationExit = Invoke-Validator -Root $targetRoot
    Assert-True -Condition ($invalidConfirmationExit -ne 0) -Name 'Confirmed case is rejected when a user-confirmation gate is incomplete'
    Set-Content -LiteralPath $casePath -Value $caseContent -Encoding UTF8

    $waiverPath = Join-Path $errorDirectory 'err-manual-001.md'
    $waiverContent = @'
---
known_error_version: 1
id: ERR-MANUAL-001
status: verified
severity: low
reported_at: 2026-08-28T11:00:00-07:00
resolved_at: 2026-08-28T12:20:00-07:00
root_cause_status: confirmed
regression_test_case: none
regression_test_path: none
regression_test_command: none
last_verified_at: 2026-08-28T12:20:00-07:00
verified_against: fixture-state-001
waiver_status: user-approved
waiver_confirmed_at: 2026-08-28T12:15:00-07:00
superseded_by: none
---

# ERR-MANUAL-001 - Manual-only verification

## Symptom

The device-only interaction previously failed.

## Trigger

Use the unavailable physical test device.

## Evidence

The user supplied concise device evidence.

## Expected behavior

The interaction completes on the physical device.

## Root cause

The device adapter used an invalid sequence.

## Resolution

The adapter sequence was corrected.

## Verification

Automation is infeasible in this fixture.

## Manual verification

The user approved running the documented device sequence and confirmed the expected result; review again when device automation becomes available.

## Notes

Residual risk is limited to the unavailable device path.
'@
    Set-Content -LiteralPath $waiverPath -Value $waiverContent -Encoding UTF8
    $waiverExit = Invoke-Validator -Root $targetRoot
    Assert-True -Condition ($waiverExit -eq 0) -Name 'User-approved manual verification waiver validates'
}
finally {
    if (Test-Path -LiteralPath $testRoot -PathType Container) {
        $resolvedTestRoot = [System.IO.Path]::GetFullPath($testRoot)
        $resolvedTempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
        if (-not $resolvedTestRoot.StartsWith($resolvedTempRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to remove a test directory outside the OS temp root: $resolvedTestRoot"
        }
        Remove-Item -LiteralPath $resolvedTestRoot -Recurse -Force
    }
}

if ($failures.Count -gt 0) {
    Write-Output "[FAIL] $($failures.Count) change-test governance test(s) failed."
    exit 1
}

Write-Output '[PASS] All change-test governance tests passed.'
exit 0
