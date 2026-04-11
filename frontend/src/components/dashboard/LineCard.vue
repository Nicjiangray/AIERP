<template>
  <div class="chart-card">
    <div class="card-title">{{ card.title }}</div>
    <v-chart
      v-if="!card.error && card.data?.length"
      class="chart"
      :option="chartOption"
      autoresize
    />
    <div v-else-if="card.error" class="card-error">{{ card.error }}</div>
    <div v-else class="card-empty text-2">暂无数据</div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

use([LineChart, GridComponent, TooltipComponent, CanvasRenderer])

const props = defineProps({ card: { type: Object, required: true } })

const chartOption = computed(() => {
  const data = props.card.data || []
  const color = props.card.card_config?.color || '#1976d2'
  const xData = data.map((r) => (r.date != null ? r.date : r.label))
  return {
    tooltip: { trigger: 'axis' },
    grid: { left: 48, right: 16, top: 16, bottom: 32 },
    xAxis: { type: 'category', data: xData, boundaryGap: false },
    yAxis: { type: 'value' },
    series: [
      {
        type: 'line',
        smooth: true,
        data: data.map((r) => r.value),
        itemStyle: { color },
        lineStyle: { color },
        areaStyle: { opacity: 0.08, color },
      },
    ],
  }
})
</script>

<style scoped>
.chart-card {
  background: var(--bg-panel);
  border-radius: var(--radius);
  padding: 16px;
  border: 1px solid var(--border);
}
.card-title {
  font-size: 13px;
  color: var(--text-3);
  margin-bottom: 8px;
}
.chart {
  height: 250px;
  width: 100%;
}
.card-error {
  font-size: 12px;
  color: #f44336;
}
.card-empty {
  padding: 40px;
  text-align: center;
}
</style>
