export type Basis = { type: 'user_input' | 'memory' | 'user_message' | 'document_chunk'; id?: string | null; version?: string | null; revision?: number | null }
export type CandidateDraft = { question: string; options: { id: string; label: string }[]; selected_option: string; reasons: string[]; assumptions: string[]; review_conditions: string[] }
export type CandidateResult = { classification: 'decision' | 'plan' | 'consideration' | 'negation' | 'ai_suggestion' | 'unrelated'; readiness: 'ready' | 'incomplete' | 'no_decision'; reply: string; candidate: CandidateDraft | null; missing_fields: string[]; basis: Basis[]; confirmed: false; evidence_status: 'unverified' }

export type DecisionAvailability = 'available' | 'available_original_version' | 'unavailable'
export type DecisionBasis = Basis & { document_id?: string; knowledge_base_id?: string; page?: number | null; locator?: { section?: string[]; line_start?: number; line_end?: number } }
export type DecisionRevision = {
  version: number; status: 'active' | 'withdrawn'; confirmation: 'confirmed' | 'revoked'
  revision_position: 'current' | 'superseded'; availability: DecisionAvailability; evidence_status: 'unverified'
  created_at: string; confirmed_at?: string | null; actor_id?: string
  content?: CandidateDraft & { change_reason: string }; basis?: DecisionBasis[]
}
export type Decision = { id: string; current_version: number; status: 'active' | 'withdrawn'; created_at: string; updated_at: string; revision: DecisionRevision }
export type DecisionPage<T> = { items: T[]; offset: number; limit: number }
