export default function LogsPage() {
  return (
    <iframe
      src="/logs"
      style={{
        width: "100%", height: "calc(100vh - 48px)",
        border: "none", background: "#0a0a0a",
      }}
      title="实时日志"
    />
  );
}
