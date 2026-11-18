export type ActionStatus = 'todo' | 'in_progress' | 'completed' | 'cancelled'
export type ActionContent = { title: string; description: string; change_reason?: string }
export type ActionRevision = { version: number; status: ActionStatus; operation: string; actor_id: string; created_at: string; content?: ActionContent; due_date?: string | null }
export type Action = { id: string; current_version: number; status: ActionStatus; source_state: string; source?: { id: string; version: number }; approved_by: string; approved_at: string; created_at: string; updated_at: string; completed_at?: string | null; cancelled_at?: string | null; revision: ActionRevision }
export const actionLabels: Record<ActionStatus, string> = { todo: '待执行', in_progress: '进行中', completed: '已完成', cancelled: '已取消' }
export const transitions: Record<ActionStatus, ActionStatus[]> = { todo: ['in_progress', 'completed', 'cancelled'], in_progress: ['todo', 'completed', 'cancelled'], completed: ['todo'], cancelled: ['todo'] }
