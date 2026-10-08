' TaiPlan silent launcher
' ASCII only + CRLF on purpose: Windows Script Host mis-parses LF-only files
' and UTF-8 Chinese comments (they can swallow the following line).
' This script only starts the project venv pythonw with launcher.py.
Option Explicit

Dim fso, shell, baseDir, pythonw, launcher, cmd
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

baseDir = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw = fso.BuildPath(baseDir, ".venv\Scripts\pythonw.exe")
launcher = fso.BuildPath(baseDir, "launcher.py")

If Not fso.FolderExists(baseDir) Then
    MsgBox "TaiPlan could not start: project folder not found." & vbCrLf & baseDir, 16, "TaiPlan"
    WScript.Quit 2
End If

If Not fso.FileExists(pythonw) Then
    MsgBox "TaiPlan could not start: venv pythonw.exe not found." & vbCrLf & pythonw, 16, "TaiPlan"
    WScript.Quit 3
End If

If Not fso.FileExists(launcher) Then
    MsgBox "TaiPlan could not start: launcher.py not found." & vbCrLf & launcher, 16, "TaiPlan"
    WScript.Quit 4
End If

' 0 = hidden window, False = do not wait
shell.CurrentDirectory = baseDir
cmd = """" & pythonw & """ """ & launcher & """"
shell.Run cmd, 0, False
