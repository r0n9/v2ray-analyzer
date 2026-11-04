const { createApp, ref, onMounted, nextTick, watch } = Vue;

createApp({
  setup() {
    const activeTab = ref('overview');
    const selectedDate = ref('');
    const availableDates = ref([]);
    const syncing = ref(false);
    const lastDataVersion = ref(null);

    // Overview state
    const overview = ref({
      total_requests: 0,
      unique_ips: 0,
      unique_domains: 0,
      hourly_distribution: Array(24).fill(0),
      protocol_distribution: {},
      outbound_distribution: {},
      top_domains: [],
      top_target_ips: [],
      top_ips: [],
    });

    // IP List state
    const ipList = ref([]);
    const ipTotal = ref(0);
    const ipPage = ref(1);
    const currentIpDetail = ref(null);
    const editingAlias = ref(null);

    // Logs Explorer state
    const logFilters = ref({
      date: '',
      client_ip: '',
      target_host: '',
      target_port: null,
      network: '',
    });
    const logResults = ref([]);
    const logTotal = ref(0);
    const logPage = ref(1);

    // Chart instances
    let hourlyChart = null;
    let domainChart = null;
    let targetIpChart = null;
    let ipHourlyChart = null;

    // Fetch Overview Data
    const fetchOverview = async () => {
      try {
        const url = selectedDate.value
          ? `/api/stats/overview?date=${selectedDate.value}`
          : '/api/stats/overview';
        const res = await fetch(url);
        if (!res.ok) throw new Error('Failed to load overview data');
        const data = await res.json();
        overview.value = data;
        if (data.version) {
          lastDataVersion.value = data.version;
        }
        if (!selectedDate.value && data.date) {
          selectedDate.value = data.date;
        }
        if (data.available_dates) {
          availableDates.value = data.available_dates;
        }

        await nextTick();
        renderOverviewCharts();
      } catch (err) {
        console.error('Error fetching overview:', err);
      }
    };

    // Render Charts
    const renderOverviewCharts = () => {
      // 1. Hourly Trend Chart
      const hourlyDom = document.getElementById('hourlyChart');
      if (hourlyDom) {
        if (!hourlyChart) {
          hourlyChart = echarts.init(hourlyDom);
        }
        const hours = Array.from({ length: 24 }, (_, i) => `${i}:00`);
        const values = overview.value.hourly_distribution || Array(24).fill(0);

        hourlyChart.setOption({
          backgroundColor: 'transparent',
          tooltip: {
            trigger: 'axis',
            backgroundColor: '#1e293b',
            borderColor: '#334155',
            textStyle: { color: '#f8fafc' },
            formatter: '{b} <br/> 请求量: <b>{c}</b> 次',
          },
          grid: {
            left: '3%',
            right: '4%',
            bottom: '3%',
            top: '8%',
            containLabel: true,
          },
          xAxis: {
            type: 'category',
            data: hours,
            axisLine: { lineStyle: { color: '#334155' } },
            axisLabel: { color: '#94a3b8', interval: 2 },
          },
          yAxis: {
            type: 'value',
            splitLine: { lineStyle: { color: '#1e293b' } },
            axisLabel: { color: '#94a3b8' },
          },
          series: [
            {
              name: '请求数',
              type: 'line',
              smooth: true,
              data: values,
              symbolSize: 6,
              itemStyle: { color: '#6366f1' },
              lineStyle: { width: 3, color: '#6366f1' },
              areaStyle: {
                color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                  { offset: 0, color: 'rgba(99, 102, 241, 0.45)' },
                  { offset: 1, color: 'rgba(99, 102, 241, 0.02)' },
                ]),
              },
            },
          ],
        });
      }

      // 2. Top Domains Bar Chart
      const domainDom = document.getElementById('domainChart');
      if (domainDom) {
        if (!domainChart) {
          domainChart = echarts.init(domainDom);
        }
        const domains = (overview.value.top_domains || []).slice().reverse();
        const yData = domains.map((d) => d.domain);
        const xData = domains.map((d) => d.count);

        domainChart.setOption({
          backgroundColor: 'transparent',
          tooltip: {
            trigger: 'axis',
            axisPointer: { type: 'shadow' },
            backgroundColor: '#1e293b',
            borderColor: '#334155',
            textStyle: { color: '#f8fafc' },
            formatter: '{b} <br/> 请求数: <b>{c}</b> 次',
          },
          grid: {
            left: '3%',
            right: '6%',
            bottom: '3%',
            top: '3%',
            containLabel: true,
          },
          xAxis: {
            type: 'value',
            splitLine: { lineStyle: { color: '#1e293b' } },
            axisLabel: { color: '#94a3b8' },
          },
          yAxis: {
            type: 'category',
            data: yData,
            axisLine: { lineStyle: { color: '#334155' } },
            axisLabel: {
              color: '#cbd5e1',
              formatter: (val) => (val.length > 22 ? val.slice(0, 20) + '...' : val),
            },
          },
          series: [
            {
              type: 'bar',
              data: xData,
              itemStyle: {
                borderRadius: [0, 4, 4, 0],
                color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [
                  { offset: 0, color: '#3b82f6' },
                  { offset: 1, color: '#10b981' },
                ]),
              },
            },
          ],
        });
      }

      // 3. Top Target IPs Bar Chart
      const targetIpDom = document.getElementById('targetIpChart');
      if (targetIpDom) {
        if (!targetIpChart) {
          targetIpChart = echarts.init(targetIpDom);
        }
        const ips = (overview.value.top_target_ips || []).slice().reverse();
        const yData = ips.map((d) => d.ip);
        const xData = ips.map((d) => d.count);

        targetIpChart.setOption({
          backgroundColor: 'transparent',
          tooltip: {
            trigger: 'axis',
            axisPointer: { type: 'shadow' },
            backgroundColor: '#1e293b',
            borderColor: '#334155',
            textStyle: { color: '#f8fafc' },
            formatter: '{b} <br/> 请求数: <b>{c}</b> 次',
          },
          grid: {
            left: '3%',
            right: '6%',
            bottom: '3%',
            top: '3%',
            containLabel: true,
          },
          xAxis: {
            type: 'value',
            splitLine: { lineStyle: { color: '#1e293b' } },
            axisLabel: { color: '#94a3b8' },
          },
          yAxis: {
            type: 'category',
            data: yData,
            axisLine: { lineStyle: { color: '#334155' } },
            axisLabel: {
              color: '#cbd5e1',
              formatter: (val) => (val.length > 22 ? val.slice(0, 20) + '...' : val),
            },
          },
          series: [
            {
              type: 'bar',
              data: xData,
              itemStyle: {
                borderRadius: [0, 4, 4, 0],
                color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [
                  { offset: 0, color: '#8b5cf6' },
                  { offset: 1, color: '#ec4899' },
                ]),
              },
            },
          ],
        });
      }
    };

    // Fetch IP List (paginated)
    const fetchIps = async (page = 1) => {
      ipPage.value = page;
      try {
        const params = new URLSearchParams({ page: page.toString(), page_size: '50' });
        if (selectedDate.value) params.append('date', selectedDate.value);
        const res = await fetch(`/api/stats/ips?${params.toString()}`);
        if (!res.ok) throw new Error('Failed to fetch IPs');
        const data = await res.json();
        ipList.value = data.ips || [];
        ipTotal.value = data.total || 0;
      } catch (err) {
        console.error('Error fetching IPs:', err);
      }
    };

    // Open IP Detail Drill Down
    const openIpDetail = async (ip) => {
      try {
        const url = selectedDate.value
          ? `/api/stats/ip/${ip}?date=${selectedDate.value}`
          : `/api/stats/ip/${ip}`;
        const res = await fetch(url);
        if (!res.ok) throw new Error('Failed to fetch IP details');
        currentIpDetail.value = await res.json();

        await nextTick();
        renderIpChart();
      } catch (err) {
        console.error('Error loading IP detail:', err);
      }
    };

    const renderIpChart = () => {
      const dom = document.getElementById('ipHourlyChart');
      if (!dom || !currentIpDetail.value) return;

      if (ipHourlyChart) {
        ipHourlyChart.dispose();
      }
      ipHourlyChart = echarts.init(dom);

      const hours = Array.from({ length: 24 }, (_, i) => `${i}:00`);
      const values = currentIpDetail.value.hourly_distribution || Array(24).fill(0);

      ipHourlyChart.setOption({
        backgroundColor: 'transparent',
        tooltip: {
          trigger: 'axis',
          backgroundColor: '#1e293b',
          borderColor: '#334155',
          textStyle: { color: '#f8fafc' },
        },
        grid: {
          left: '3%',
          right: '4%',
          bottom: '3%',
          top: '10%',
          containLabel: true,
        },
        xAxis: {
          type: 'category',
          data: hours,
          axisLine: { lineStyle: { color: '#334155' } },
          axisLabel: { color: '#94a3b8', interval: 2 },
        },
        yAxis: {
          type: 'value',
          splitLine: { lineStyle: { color: '#1e293b' } },
          axisLabel: { color: '#94a3b8' },
        },
        series: [
          {
            name: '请求频次',
            type: 'bar',
            data: values,
            itemStyle: {
              borderRadius: [4, 4, 0, 0],
              color: '#818cf8',
            },
          },
        ],
      });
    };

    // Open & Save Alias
    const openAliasModal = (ip, alias) => {
      editingAlias.value = {
        ip,
        alias: alias || '',
      };
    };

    const saveAlias = async () => {
      if (!editingAlias.value) return;
      try {
        const res = await fetch('/api/alias', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(editingAlias.value),
        });
        if (res.ok) {
          // Update local state across all views
          const target = ipList.value.find((i) => i.client_ip === editingAlias.value.ip);
          if (target) target.alias = editingAlias.value.alias;

          const topTarget = overview.value.top_ips?.find((i) => i.ip === editingAlias.value.ip);
          if (topTarget) topTarget.alias = editingAlias.value.alias;

          if (currentIpDetail.value && currentIpDetail.value.client_ip === editingAlias.value.ip) {
            currentIpDetail.value.alias = editingAlias.value.alias;
          }
          editingAlias.value = null;
        }
      } catch (err) {
        console.error('Error saving alias:', err);
      }
    };

    // Logs Search
    const searchLogs = async (page = 1) => {
      logPage.value = page;
      const params = new URLSearchParams({
        page: page.toString(),
        page_size: '50',
      });
      if (logFilters.value.date) params.append('date', logFilters.value.date);
      if (logFilters.value.client_ip) params.append('client_ip', logFilters.value.client_ip.trim());
      if (logFilters.value.target_host) params.append('target_host', logFilters.value.target_host.trim());
      if (logFilters.value.target_port) params.append('target_port', logFilters.value.target_port.toString());
      if (logFilters.value.network) params.append('network', logFilters.value.network);

      try {
        const res = await fetch(`/api/logs?${params.toString()}`);
        if (!res.ok) throw new Error('Search failed');
        const data = await res.json();
        logResults.value = data.logs || [];
        logTotal.value = data.total || 0;
      } catch (err) {
        console.error('Error searching logs:', err);
      }
    };

    // Manual Sync
    const triggerManualSync = async () => {
      syncing.value = true;
      try {
        const res = await fetch('/api/sync', { method: 'POST' });
        if (res.ok) {
          await fetchOverview();
          await fetchIps(1);
          if (activeTab.value === 'logs') {
            await searchLogs(1);
          }
        }
      } catch (err) {
        console.error('Sync failed:', err);
      } finally {
        syncing.value = false;
      }
    };

    const onDateChange = () => {
      fetchOverview();
      fetchIps(1);
      if (logFilters.value.date === '') {
        // optionally align
      }
    };

    // Watch tab change to resize or refresh
    watch(activeTab, (tab) => {
      if (tab === 'overview') {
        nextTick(() => {
          if (hourlyChart) hourlyChart.resize();
          if (domainChart) domainChart.resize();
          if (targetIpChart) targetIpChart.resize();
        });
      } else if (tab === 'ips') {
        fetchIps(1);
      } else if (tab === 'logs' && logResults.value.length === 0) {
        searchLogs(1);
      }
    });

    onMounted(async () => {
      await fetchOverview();
      await fetchIps(1);

      window.addEventListener('resize', () => {
        if (hourlyChart) hourlyChart.resize();
        if (domainChart) domainChart.resize();
        if (targetIpChart) targetIpChart.resize();
        if (ipHourlyChart) ipHourlyChart.resize();
      });

      // Smart polling: check lightweight version (O(1) memory check) every 15s
      // Only pull heavy overview SQL analytics when data version actually changed
      setInterval(async () => {
        if (syncing.value) return;
        if (activeTab.value !== 'overview' && activeTab.value !== 'ips') return;

        try {
          const res = await fetch('/api/stats/version');
          if (!res.ok) return;
          const { version } = await res.json();

          if (lastDataVersion.value !== null && version !== lastDataVersion.value) {
            lastDataVersion.value = version;
            if (activeTab.value === 'overview') {
              await fetchOverview();
            } else if (activeTab.value === 'ips') {
              await fetchIps(ipPage.value);
            }
          } else if (lastDataVersion.value === null) {
            lastDataVersion.value = version;
          }
        } catch (err) {
          console.debug('Version check skipped:', err);
        }
      }, 15000);
    });

    return {
      activeTab,
      selectedDate,
      availableDates,
      syncing,
      overview,
      ipList,
      ipTotal,
      ipPage,
      currentIpDetail,
      editingAlias,
      logFilters,
      logResults,
      logTotal,
      logPage,
      onDateChange,
      fetchOverview,
      fetchIps,
      openIpDetail,
      openAliasModal,
      saveAlias,
      searchLogs,
      triggerManualSync,
    };
  },
}).mount('#app');
