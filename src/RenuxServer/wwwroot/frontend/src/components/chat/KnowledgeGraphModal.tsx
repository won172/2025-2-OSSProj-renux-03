import React, { useEffect, useRef, useState } from 'react';

interface GraphNode {
  id: string;
  name: string;
  type: string;
  description?: string;
  x?: number;
  y?: number;
}

interface GraphEdge {
  source: string;
  target: string;
  relation: string;
  description?: string;
}

interface KnowledgeGraphModalProps {
  isOpen: boolean;
  onClose: () => void;
  keyword: string;
}

export const KnowledgeGraphModal: React.FC<KnowledgeGraphModalProps> = ({
  isOpen,
  onClose,
  keyword
}) => {
  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [edges, setEdges] = useState<GraphEdge[]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    if (!isOpen || !keyword) return;

    setLoading(true);
    fetch(`/api/v2/graph?keyword=${encodeURIComponent(keyword)}&max_hops=2`)
      .then(res => res.json())
      .then(data => {
        setNodes(data.nodes || []);
        setEdges(data.edges || []);
        setLoading(false);
      })
      .catch(err => {
        console.error('Failed to fetch v2 entity graph:', err);
        setLoading(false);
      });
  }, [isOpen, keyword]);

  // Simple Force Layout Simulation on Canvas
  useEffect(() => {
    if (!isOpen || nodes.length === 0 || !canvasRef.current) return;

    const canvas = canvasRef.current;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const width = canvas.width;
    const height = canvas.height;

    // Position nodes in a radial force layout
    const positionedNodes = nodes.map((node, i) => {
      const angle = (i / nodes.length) * 2 * Math.PI;
      const radius = 120 + (i % 3) * 40;
      return {
        ...node,
        x: width / 2 + Math.cos(angle) * radius,
        y: height / 2 + Math.sin(angle) * radius
      };
    });

    // Render Canvas Loop
    ctx.clearRect(0, 0, width, height);

    // Draw Edges
    ctx.strokeStyle = '#475569';
    ctx.lineWidth = 1.5;
    edges.forEach(edge => {
      const srcNode = positionedNodes.find(n => n.id === edge.source);
      const tgtNode = positionedNodes.find(n => n.id === edge.target);
      if (srcNode && tgtNode) {
        ctx.beginPath();
        ctx.moveTo(srcNode.x, srcNode.y);
        ctx.lineTo(tgtNode.x, tgtNode.y);
        ctx.stroke();
      }
    });

    // Draw Nodes
    positionedNodes.forEach(node => {
      ctx.beginPath();
      let color = '#3b82f6'; // course blue
      if (node.type === 'department') color = '#8b5cf6'; // dept purple
      else if (node.type === 'rule') color = '#f59e0b'; // rule amber
      else if (node.type === 'concept') color = '#10b981'; // concept green

      ctx.arc(node.x, node.y, 16, 0, 2 * Math.PI);
      ctx.fillStyle = color;
      ctx.fill();
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 2;
      ctx.stroke();

      // Label text
      ctx.fillStyle = '#f8fafc';
      ctx.font = '12px sans-serif';
      ctx.fillText(node.name.slice(0, 8), node.x - 20, node.y + 30);
    });
  }, [isOpen, nodes, edges]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
      <div className="relative w-full max-w-4xl bg-slate-900 border border-slate-700 rounded-2xl shadow-2xl overflow-hidden text-slate-100">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-800 bg-slate-950/80">
          <div className="flex items-center gap-3">
            <span className="text-xl">🕸️</span>
            <h3 className="text-lg font-semibold tracking-tight">
              옵시디언 지식 네트워크: <span className="text-purple-400">"{keyword}"</span>
            </h3>
          </div>
          <button
            onClick={onClose}
            className="p-2 text-slate-400 hover:text-white rounded-lg hover:bg-slate-800 transition"
          >
            ✕
          </button>
        </div>

        {/* Canvas & Sidebar Body */}
        <div className="flex flex-col md:flex-row h-[500px]">
          <div className="relative flex-1 bg-slate-950 flex items-center justify-center">
            {loading ? (
              <div className="flex items-center gap-3 text-slate-400">
                <div className="w-5 h-5 border-2 border-purple-500 border-t-transparent rounded-full animate-spin"></div>
                <span>지식 서브그래프 탐색 중...</span>
              </div>
            ) : (
              <canvas
                ref={canvasRef}
                width={600}
                height={480}
                className="w-full h-full cursor-grab active:cursor-grabbing"
              />
            )}
          </div>

          {/* Details Sidebar */}
          <div className="w-full md:w-80 border-t md:border-t-0 md:border-l border-slate-800 p-6 bg-slate-900/90 overflow-y-auto">
            <h4 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-4">
              📊 서브그래프 메타데이터
            </h4>
            <div className="space-y-3 text-sm">
              <div className="flex justify-between py-2 border-b border-slate-800">
                <span className="text-slate-400">탐색된 엔티티 노드</span>
                <span className="font-semibold text-purple-400">{nodes.length}개</span>
              </div>
              <div className="flex justify-between py-2 border-b border-slate-800">
                <span className="text-slate-400">연결된 학사 간선</span>
                <span className="font-semibold text-blue-400">{edges.length}개</span>
              </div>
            </div>

            <div className="mt-6">
              <h5 className="text-xs font-semibold text-slate-400 mb-2">노드 범례 (Obsidian Types)</h5>
              <div className="grid grid-cols-2 gap-2 text-xs">
                <div className="flex items-center gap-2"><span className="w-3 h-3 rounded-full bg-purple-500"></span>학과 (Dept)</div>
                <div className="flex items-center gap-2"><span className="w-3 h-3 rounded-full bg-blue-500"></span>교과목 (Course)</div>
                <div className="flex items-center gap-2"><span className="w-3 h-3 rounded-full bg-amber-500"></span>학칙 (Rule)</div>
                <div className="flex items-center gap-2"><span className="w-3 h-3 rounded-full bg-emerald-500"></span>개념 (Concept)</div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
