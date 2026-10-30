export type Json = Record<string, unknown>;

/** One styled piece of text. Styles a client does not know are dropped; the text never is. */
export interface DisplayRun { text: string; bold: boolean; italic?: boolean; strike?: boolean; code?: 'inline' | 'block';
  language?: string; heading?: number; href?: string; quote?: boolean }
export interface DisplayBlock { id: string; text: string; complete: boolean; runs?: DisplayRun[] }
export interface DisplayProjection {
  version: 1;
  id: string;
  input_revision: string;
  turn_id: string;
  conversation: string;
  author: { kind: string; name: string; label: string };
  kind: string;
  placement: 'chat' | 'work' | 'source';
  status: string;
  status_label: string;
  complete: boolean;
  /** Final text the owner cut into: only part of the reply was said. */
  interrupted?: boolean;
  blocks: DisplayBlock[];
  order: [number, string, number, string];
}

export interface Endpoint {
  runtimeId: string;
  spaceId: string;
  name: string;
  url: string;
  token: string;
  meshToken?: string;
  capabilities: string[];
}

export interface Message extends Json {
  id: string;
  conversation: string;
  text: string;
  status: string;
  rev: number;
  created_at: number;
  scope?: string;
  task?: string;
  partial?: string;
  author?: { kind?: string; name?: string };
  source?: { type?: string; runtime_id?: string };
  presentation?: DisplayProjection;
}

export interface SyncPage extends Json {
  runtime_id: string;
  subject_id: string;
  epoch: string;
  seq: number | string;
  reset: boolean;
  more: boolean;
  messages: Message[];
  tasks: Json[];
  approvals: Json[];
  removed: unknown;
}

export interface Pending {
  clientId: string;
  runtimeId: string;
  spaceId: string;
  conversation: string;
  payload: { text: string; conversation: string; client_id: string; card?: string; pin?: boolean; reply_scope?: 'main' };
  state: 'pending' | 'accepted' | 'blocked';
  messageId?: string;
  createdAt: number;
  error?: string;
}

export interface SpaceView {
  id: string;
  name: string;
  runtimeName: string;
  connected: boolean;
  catchingUp: boolean;
  error: string;
  conversations: Conversation[];
  selectedConversation: string;
  messages: Message[];
  tasks: Json[];
  approvals: Json[];
  pending: Pending[];
  helpers?: HelperView[];
  connections?: { runtime: string; name: string; local: boolean; connected: boolean; capabilities: string[] }[];
}
export interface Conversation { id: string; title: string; createdAt: number; updatedAt?: number; revision?: number;
  metadataAt?: number; metadataRevision?: number; directoryId?: string; pinned?: boolean; archived?: boolean; deleted?: boolean; origin?: string; context?: Json; unread?: boolean; pending?: boolean }
export interface HelperView {
  members?: HelperView[];
  id: string; runtime: string; device: string; name: string; label: string; type: string;
  model: string; effort: string; connected: boolean; updatedAt: number; quotaProblem?: string;
  fiveHour: { left: number | null; resetsAt: number | null }; week: { left: number | null; resetsAt: number | null };
}

export interface LocalAgent {
  id: 'dsh' | 'pi' | 'codex' | 'claude'; name: string; installed: boolean; configured: boolean;
  auth: 'signed-in' | 'signed-out' | 'unknown'; version: string; location: string;
  command?: string[]; bundled?: boolean;
}
export interface LocalService { id: string; name: string; source: string; agents: string[]; managed: boolean }
export interface SetupScan { agents: LocalAgent[]; services: LocalService[]; hasDeepSeekKey: boolean }
export interface SetupView {
  confirmed: boolean; savedConnections: { name: string; route: string }[];
  service: 'none' | 'bundled' | 'external'; device: string;
  firstRun?: FirstRunView;
}
export interface FirstRunView {
  required: boolean; phase: 'key' | 'validating' | 'preparing' | 'reply' | 'ready' | 'failed';
  reply: string; problem: string; hasKey: boolean; canEditKey: boolean; selectedAgents?: string[];
}

export interface ClientView {
  account?: AccountView;
  version: string;
  spaces: SpaceView[];
  selectedSpace: string;
  background: boolean;
  setup?: SetupView;
  relayHost?: RelayHostView;
  runtime: { state: string; message: string };
  pairing: { invitation?: string; joined: number; relayState: string;
    requests: { id: string; name: string; fingerprint: string; expires?: number; computer?: boolean }[] };
}
export interface AccountView {
  signedIn: boolean; username: string; busy: boolean; message: string; canInvite: boolean;
  provisioned: boolean; requiresProvisioning?: boolean;
  pairing: { invitation?: string; joined: number; relayState: string;
    requests: { id: string; name: string; fingerprint: string; expires?: number; computer?: boolean }[] };
}

export interface RelayHostView {
  enabled: boolean; listening: boolean; port: number; publicURL: string;
  publicCheck: 'not-run' | 'computer-reachable' | 'failed';
  error: string; fingerprint: string; expiresAt: number; localAddresses: string[];
}

export interface PhoneConnection {
  routes: { url: string; kind: 'lan' | 'private-network' }[];
  lan: 'unavailable' | 'computer-verified';
  remote: 'unavailable' | 'existing-private-network'; note: string;
}
export interface DesktopAPI {
  accountLogin(input: { username: string; password: string; register: boolean }): Promise<void>;
  accountLogout(): Promise<void>;
  accountRefresh(): Promise<void>;
  startRelayHost(input: { port: number; publicURL: string }): Promise<void>;
  stopRelayHost(): Promise<void>;
  checkRelayHost(): Promise<void>;
  invitationQR(link: string): Promise<{ dataURL: string; expiresAt: number }>;
  setRelay(input: { url: string; token: string }): Promise<void>;
  phoneConnection(): Promise<PhoneConnection>;
  copyPhoneConnection(url: string): Promise<void>;
  snapshot(): Promise<ClientView>;
  scanLocal(): Promise<SetupScan>;
  enableLocal(input: { service?: string; agents: string[]; deepseekKey?: string }): Promise<void>;
  startFirstRun(input: { key?: string; agents?: string[] }): Promise<void>;
  checkFirstRun(): Promise<void>;
  restoreConnections(): Promise<void>;
  send(input: { space: string; text: string; card?: string; runtime?: string; mainReply?: boolean }): Promise<void>;
  select(space: string): Promise<void>;
  selectConversation(input: { space: string; conversation: string }): Promise<void>;
  createConversation(input: { space: string; title: string; message?: string; runtime?: string }): Promise<void>;
  markRead(input: { space: string; conversation: string }): Promise<void>;
  taskDetail(input: { space: string; id: string; runtime: string; before?: number }): Promise<Json>;
  refreshHelpers(space: string): Promise<void>;
  directory(input: { space: string; runtime?: string; cursor?: string; query?: string }): Promise<Json>;
  directoryEdit(input: { space: string; runtime: string; id: string; revision: number; fields: Json }): Promise<Json>;
  directoryHistory(input: { space: string; runtime: string; id: string; cursor?: string }): Promise<Json>;
  directoryOpen(input: {space: string; runtime: string; id: string}): Promise<Json>;
  directoryMetadata(input: { space: string; runtime: string; id: string }): Promise<Json>;
  openNative(input: { space: string; id: string; runtime: string }): Promise<void>;
  openLink(href: string): Promise<void>;
  helperOptions(input: { runtime?: string; space: string }): Promise<Json>;
  helperModels(input: { runtime?: string; space: string; gateway_url: string; key?: string }): Promise<Json>;
  helperModel(input: { runtime?: string; space: string; name: string; model: string }): Promise<Json>;
  helperGrants(input: { runtime?: string; space: string; name: string; grants: Json }): Promise<Json>;
  addHelper(input: { runtime?: string; space: string; helper: Json }): Promise<Json>;
  removeHelper(input: { runtime?: string; space: string; name: string }): Promise<Json>;
  connectLocal(): Promise<void>;
  importConfig(): Promise<void>;
  approve(input: { space: string; id: string; runtime: string; decision: 'allow' | 'deny' }): Promise<void>;
  cancel(input: { space: string; id: string; runtime: string }): Promise<void>;
  continueSession(input: { space: string; id: string; runtime: string; text: string }): Promise<void>;
  effectReview(input: { space: string; runtime: string; record: string }): Promise<Json>;
  timerReviews(input: { space: string }): Promise<Json[]>;
  submitEffectReview(input: { space: string; runtime: string; record: string; revision: string; outcomes: Record<string, 'applied' | 'not_applied'> }): Promise<Json>;
  reconcile(input: { space: string; id: string; runtime: string; run: string }): Promise<'confirmed' | 'unconfirmed'>;
  invite(): Promise<string>;
  join(invitation: string): Promise<void>;
  acceptJoin(id: string): Promise<void>;
  configureRelay(): Promise<void>;
  loginStart(enabled: boolean): Promise<void>;
  subscribe(listener: (view: ClientView) => void): () => void;
}
