# 列出系统所有 Python / westock 进程（含命令行、父子关系、运行时长）
# 用途：替代 Git Bash 的 `ps aux | grep python`——后者只显示当前 MSYS 会话的进程，
#       看不到独立启动的 worker 与 westock 子进程（P-138）。
#
# 用法（PowerShell 或 Bash 均可）：
#   powershell -ExecutionPolicy Bypass -File scripts/list_processes.ps1
#   powershell -ExecutionPolicy Bypass -File scripts/list_processes.ps1 -Pattern minute

param(
    [string]$Pattern = ""
)

$procs = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^(python|pythonw|westock)(\.exe)?$'
}

if ($Pattern -ne "") {
    $procs = $procs | Where-Object { $_.CommandLine -match $Pattern -or $_.Name -match $Pattern }
}

$rows = foreach ($p in $procs) {
    $parent = Get-CimInstance Win32_Process -Filter "ProcessId = $($p.ParentProcessId)" -ErrorAction SilentlyContinue
    [PSCustomObject]@{
        PID      = $p.ProcessId
        PPID     = $p.ParentProcessId
        Parent   = if ($parent) { $parent.Name } else { "-" }
        Name     = $p.Name
        Started  = $p.CreationDate
        Command  = if ($p.CommandLine) { $p.CommandLine } else { "(空)" }
    }
}

$rows = $rows | Sort-Object Started
foreach ($r in $rows) {
    Write-Output ("PID={0}  PPID={1}  Parent={2}  Name={3}  Started={4}" -f $r.PID, $r.PPID, $r.Parent, $r.Name, $r.Started)
    Write-Output ("    CMD: {0}" -f $r.Command)
}
Write-Output ""
Write-Output ("共 {0} 个进程" -f @($rows).Count)
