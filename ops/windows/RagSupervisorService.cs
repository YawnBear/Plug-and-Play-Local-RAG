using System;
using System.Diagnostics;
using System.IO;
using System.ServiceProcess;

namespace LocalRag
{
    internal sealed class RagSupervisorService : ServiceBase
    {
        private Process child;
        private volatile bool stopping;

        internal RagSupervisorService()
        {
            ServiceName = "RagSupervisor";
            CanStop = true;
            CanShutdown = true;
            AutoLog = true;
        }

        protected override void OnStart(string[] args)
        {
            string root = Environment.GetEnvironmentVariable("ProgramFiles");
            string release = Path.Combine(root, "LocalRAG", "current");
            string python = Path.Combine(release, "runtimes", "api-python", "python.exe");
            string manifest = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData),
                "LocalRAG", "installed-deployment.json");
            if (!File.Exists(python) || !File.Exists(manifest))
                throw new InvalidOperationException("Local RAG runtime or installed manifest is missing");

            child = new Process();
            child.StartInfo.FileName = python;
            child.StartInfo.Arguments = "-m apps.supervisor run-foreground --manifest \"" + manifest + "\"";
            child.StartInfo.WorkingDirectory = release;
            child.StartInfo.UseShellExecute = false;
            child.StartInfo.CreateNoWindow = true;
            child.EnableRaisingEvents = true;
            child.Exited += (sender, eventArgs) =>
            {
                if (stopping || !child.HasExited)
                    return;
                int failure = child.ExitCode == 0 ? 1 : child.ExitCode;
                ExitCode = failure;
                Environment.Exit(failure);
            };
            if (!child.Start())
                throw new InvalidOperationException("Python supervisor did not start");
        }

        protected override void OnStop()
        {
            stopping = true;
            if (child == null || child.HasExited)
                return;
            child.Kill();
            if (!child.WaitForExit(30000))
                throw new System.TimeoutException("Python supervisor did not stop within 30 seconds");
            child.Dispose();
            child = null;
        }

        protected override void OnShutdown() { OnStop(); }

        public static void Main() { ServiceBase.Run(new RagSupervisorService()); }
    }
}
