' Launch PDFTOOL with pythonw so Windows uses the Start Menu shortcut icon
' (AppUserModelID is also set in Python for taskbar branding).
Option Explicit
Dim sh, fso, root, pythonw, script, cmd
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
script = root & "\pdf_editor.py"

' Prefer pythonw.exe next to python on PATH
pythonw = "pythonw.exe"
On Error Resume Next
Dim which
which = sh.Exec("where pythonw").StdOut.ReadLine
If Len(Trim(which)) > 0 Then pythonw = Trim(which)
On Error GoTo 0

cmd = """" & pythonw & """ """ & script & """"
sh.CurrentDirectory = root
sh.Run cmd, 1, False
