import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts'
import { useStore } from '../store/useStore'

function formatTime(ts: number) {
  const d = new Date(ts)
  return d.getSeconds().toString().padStart(2, '0') + 's'
}

const tooltipStyle = {
  background: '#141414',
  border: '1px solid #2a2a2a',
  borderRadius: 0,
  fontFamily: '"JetBrains Mono", monospace',
  fontSize: 10,
  color: '#f0f0f0',
}

const labelStyle = { color: '#888', fontSize: 9, fontFamily: '"JetBrains Mono", monospace' }

export default function LatencyGraph() {
  const data = useStore((s) => s.latencyHistory)

  return (
    <div className="panel">
      <div className="panel-header">/ LATENCY MONITOR</div>
      <div className="panel-body" style={{ padding: '8px 4px 4px 0' }}>
        {data.length < 2 ? (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              height: '100%',
              color: 'var(--text-secondary)',
              fontSize: 11,
              letterSpacing: '0.08em',
            }}
          >
            WAITING FOR TRAFFIC…
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data} margin={{ top: 4, right: 16, left: -10, bottom: 0 }}>
              <CartesianGrid stroke="#2a2a2a" strokeDasharray="0" />
              <XAxis
                dataKey="time"
                tickFormatter={formatTime}
                tick={labelStyle}
                axisLine={{ stroke: '#2a2a2a' }}
                tickLine={false}
                interval="preserveStartEnd"
              />
              <YAxis
                tick={labelStyle}
                axisLine={{ stroke: '#2a2a2a' }}
                tickLine={false}
                unit="ms"
                width={36}
              />
              <Tooltip
                contentStyle={tooltipStyle}
                labelStyle={labelStyle}
                labelFormatter={(v) => `t=${new Date(v as number).toLocaleTimeString()}`}
                formatter={(val: number, name: string) => [`${val}ms`, name.toUpperCase()]}
              />
              <Legend
                wrapperStyle={{
                  fontSize: 9,
                  fontFamily: '"JetBrains Mono", monospace',
                  color: '#888',
                  paddingTop: 2,
                }}
                iconType="plainline"
              />
              <Line
                type="monotone"
                dataKey="datagram"
                stroke="#C8F400"
                dot={false}
                strokeWidth={1.5}
                isAnimationActive={false}
              />
              <Line
                type="monotone"
                dataKey="stream"
                stroke="#f0f0f0"
                dot={false}
                strokeWidth={1.5}
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  )
}
