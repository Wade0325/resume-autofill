// ResumeAutoFill 啟動器：雙擊 → 拉起後端（內嵌 Python）→ 開瀏覽器 → 常駐系統匣。
// 結束時把後端連同它生的 llama-server 一起收掉，不留孤兒行程。
using System.Diagnostics;
using System.Net.Http;

namespace ResumeAutoFill.Launcher;

internal static class Program
{
    private static readonly string Root = AppContext.BaseDirectory.TrimEnd('\\', '/');
    private static Process? _backend;
    private static int _port;

    [STAThread]
    private static void Main()
    {
        // 同一份程式開兩次只會多開一個瀏覽器分頁，不會多拉一個後端
        using var mutex = new Mutex(true, "ResumeAutoFill.Launcher", out var isFirst);

        _port = int.TryParse(Environment.GetEnvironmentVariable("RESUME_AUTOFILL_API_PORT"),
                             out var p) ? p : 8090;
        Debug($"start port={_port} isFirst={isFirst}");

        if (IsOurBackendHealthy(_port) || !isFirst)
        {
            Debug($"early-exit healthy={IsOurBackendHealthy(_port)} isFirst={isFirst}");
            OpenBrowser();
            return;
        }

        // 8090 被別的程式占走時往後找一個空port（我們的後端在上面就直接沿用）
        while (PortInUse(_port)) _port++;

        try
        {
            StartBackend();
        }
        catch (Exception ex)
        {
            MessageBox.Show("後端啟動失敗：" + ex.Message, "Resume AutoFill",
                            MessageBoxButtons.OK, MessageBoxIcon.Error);
            return;
        }

        Debug($"backend started pid={_backend?.Id}");
        if (!WaitHealthy(TimeSpan.FromSeconds(60)))
        {
            Debug($"wait-timeout backendExited={_backend?.HasExited}");
            KillBackend();
            MessageBox.Show(
                "後端在 60 秒內沒有就緒。\n詳細原因請看 %USERPROFILE%\\.resume_autofill\\logs\\app.log",
                "Resume AutoFill", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return;
        }

        OpenBrowser();
        RunTray();     // 阻塞直到使用者從系統匣選「結束」
        KillBackend();
    }

    private static void Debug(string msg)
    {
        // 疑難排解用：設 RESUME_AUTOFILL_DEBUG=1 時把決策過程寫到根目錄
        if (Environment.GetEnvironmentVariable("RESUME_AUTOFILL_DEBUG") != "1") return;
        try
        {
            File.AppendAllText(Path.Combine(Root, "launcher-debug.log"),
                               $"{DateTime.Now:HH:mm:ss.fff} {msg}\r\n");
        }
        catch { }
    }

    private static void StartBackend()
    {
        var python = Path.Combine(Root, "app", "runtime", "pythonw.exe");
        var entry = Path.Combine(Root, "app", "run_backend.py");
        if (!File.Exists(python)) throw new FileNotFoundException(python);
        if (!File.Exists(entry)) throw new FileNotFoundException(entry);

        var psi = new ProcessStartInfo
        {
            FileName = python,
            Arguments = $"\"{entry}\"",
            WorkingDirectory = Root,
            UseShellExecute = false,
            CreateNoWindow = true,
        };
        // 後端所有路徑（models/、bin/、資料目錄）都從這個根推導
        psi.Environment["RESUME_AUTOFILL_ROOT"] = Root;
        psi.Environment["RESUME_AUTOFILL_API_PORT"] = _port.ToString();

        _backend = Process.Start(psi) ?? throw new InvalidOperationException("Process.Start 回傳 null");
    }

    private static void KillBackend()
    {
        try
        {
            // entireProcessTree：連後端啟動的 llama-server 一起收
            if (_backend is { HasExited: false }) _backend.Kill(entireProcessTree: true);
        }
        catch
        {
            // 已經死了就算了，結束流程不需要對使用者報錯
        }
    }

    private static bool WaitHealthy(TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            if (_backend is { HasExited: true }) return false;
            if (IsOurBackendHealthy(_port)) return true;
            Thread.Sleep(500);
        }
        return false;
    }

    private static bool IsOurBackendHealthy(int port)
    {
        try
        {
            using var http = new HttpClient { Timeout = TimeSpan.FromMilliseconds(800) };
            var body = http.GetStringAsync($"http://127.0.0.1:{port}/api/health").Result;
            return body.Contains("\"api\":\"ok\"");
        }
        catch
        {
            return false;
        }
    }

    private static bool PortInUse(int port)
    {
        try
        {
            using var client = new System.Net.Sockets.TcpClient();
            return client.ConnectAsync("127.0.0.1", port).Wait(300);
        }
        catch
        {
            return false;
        }
    }

    private static void OpenBrowser()
    {
        // 自動化測試時不彈瀏覽器
        if (Environment.GetEnvironmentVariable("RESUME_AUTOFILL_NO_BROWSER") == "1") return;
        Process.Start(new ProcessStartInfo
        {
            FileName = $"http://127.0.0.1:{_port}/",
            UseShellExecute = true,
        });
    }

    private static void RunTray()
    {
        ApplicationConfiguration.Initialize();

        using var menu = new ContextMenuStrip();
        menu.Items.Add("開啟介面", null, (_, _) => OpenBrowser());
        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add("結束", null, (_, _) => Application.Exit());

        using var tray = new NotifyIcon
        {
            Icon = System.Drawing.SystemIcons.Application,
            Text = "Resume AutoFill（雙擊開啟介面）",
            ContextMenuStrip = menu,
            Visible = true,
        };
        tray.DoubleClick += (_, _) => OpenBrowser();

        Application.Run();
        tray.Visible = false;
    }
}
