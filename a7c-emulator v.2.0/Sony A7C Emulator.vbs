Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
Set sh = CreateObject("WScript.Shell")
sh.Run "pythonw.exe """ & dir & "\Sony A7C Emulator.pyw""", 0, False
