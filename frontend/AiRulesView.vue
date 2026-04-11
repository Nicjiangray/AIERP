<template>
  <div class="view">
    <div class="view-header">
      <h1 class="view-title">AI 规则</h1>
      <span class="mono text-3">meta.ai_rules</span>
    </div>

    <div class="rules-placeholder text-2">
      <p>
        系统表（含 <span class="mono">meta.ai_rules</span>）不在 Web 界面维护，请使用数据库客户端（如
        <span class="mono">psql</span>）直接访问。
      </p>
    </div>
  </div>
</template>

<style scoped>
.view { padding: 32px 36px; max-width: 800px; }
.view-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 32px;
  padding-bottom: 20px;
  border-bottom: 1px solid var(--border);
}
.view-title { font-size: 22px; font-weight: 300; letter-spacing: 0.05em; }

.rules-placeholder {
  max-width: 520px;
  line-height: 1.7;
  padding: 8px 0;
}
.rules-placeholder .mono { color: var(--text-2); }
</style>
