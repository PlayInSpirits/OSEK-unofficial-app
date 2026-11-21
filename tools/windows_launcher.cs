using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;

internal static class Launcher
{
    [STAThread]
    private static int Main(string[] args)
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string executable = Process.GetCurrentProcess().MainModule.FileName;
        bool test = args.Length == 1 && args[0] == "--packaging-check";
        if (!test)
            CreateShortcutOnce(executable, root);

        string internalDir = Path.Combine(root, "_internal");
        var start = new ProcessStartInfo(Path.Combine(internalDir, "OSEK-App.exe"));
        start.WorkingDirectory = internalDir;
        start.UseShellExecute = false;
        if (test)
            start.Arguments = "--packaging-check";
        start.EnvironmentVariables["OSEK_LAUNCHER_PID"] = Process.GetCurrentProcess().Id.ToString();
        using (var child = Process.Start(start))
        {
            child.WaitForExit();
            return child.ExitCode;
        }
    }

    private static void CreateShortcutOnce(string executable, string root)
    {
        try
        {
            string data = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "ОСЭК-Remastered");
            string marker = Path.Combine(data, "desktop-shortcut-created");
            if (File.Exists(marker))
                return;
            string desktop = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
            if (!Directory.Exists(desktop))
                return;
            string path = Path.Combine(desktop, "ОСЭК-Remastered.lnk");
            dynamic shell = Activator.CreateInstance(Type.GetTypeFromProgID("WScript.Shell"));
            dynamic shortcut = shell.CreateShortcut(path);
            shortcut.TargetPath = executable;
            shortcut.WorkingDirectory = root;
            shortcut.IconLocation = executable + ",0";
            shortcut.Save();
            Marshal.FinalReleaseComObject(shortcut);
            Marshal.FinalReleaseComObject(shell);
            Directory.CreateDirectory(data);
            File.WriteAllText(marker, "");
        }
        catch (Exception)
        {
            // Desktop redirection or local policy must not prevent startup.
        }
    }
}
