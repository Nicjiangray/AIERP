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
import { PieChart } from 'echarts/charts'
import { TooltipComponent, LegendComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

use([PieChart, TooltipComponent, LegendComponent, CanvasRenderer])

const props = defineProps({ card: { type: Object, required: true } })

const chartOption = computed(() => {
  const data = props.card.data || []
  return {
    tooltip: { trigger: 'item' },
    legend: { bottom: 0, type: 'scroll' },
    series: [
      {
        type: 'pie',
        radius: ['36%', '68%'],
        data: data.map((r) => ({ name: r.name, value: r.value })),
        emphasis: { itemStyle: { shadowBlur: 8, shadowOffsetX: 0 } },
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
  height: 280px;
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
