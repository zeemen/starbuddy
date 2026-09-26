// 未找到：学生不存在与无权查看用同一页，不区分（spec 第 1 节）
export default function NotFound() {
  return (
    <main>
      <h1>没有找到这个页面</h1>
      <p className="muted">页面不存在，或者你没有权限查看。</p>
    </main>
  );
}
