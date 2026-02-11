import { DownloadState, GraphCategory, GraphState, LibType, UserRole } from '$lib/enums';
import { icons } from '$lib/icons';
import type { Edge, Node } from '@xyflow/svelte';
import type { HandleType, Position } from '@xyflow/system';
import type { IconifyIcon } from 'iconify-icon';

/**
 * Make all properties in T optional.
 */
export type Optional<T> = {
  [P in keyof T]?: T[P] | null;
};

/**
 * Base response interface.
 */
export interface BaseResp {
  status: number;
  message: string;
  description?: string;
}

/**
 * Response data interface.
 */
export interface Resp<T> extends BaseResp {
  request_id: string;
  data: T;
}

/**
 * A paginated list wrapper.
 */
export interface Page<T> {
  total?: number | null;
  list: T[];
}

/**
 * The type of the scroll position.
 */
export type ScrollPosition = ScrollToOptions & { panel?: boolean };

/**
 * The type of the select option value.
 */
export type OptionValue = string | number | boolean | null | undefined;

/**
 * The type of the select option.
 */
export type Option = {
  value: OptionValue;
  label: string;
  disabled?: boolean;
};

/**
 * The type of the navigation item.
 */
export type Nav = {
  title: string;
  path: string;
  icon: IconifyIcon;
  iconFilled: IconifyIcon;
  mobile: boolean;
  drawerStyle?: 'menu' | 'app';
};

/**
 * The type of the menu route item.
 */
export type MenuRoute = {
  title: string;
  path?: string;
  icon: string | IconifyIcon;
  iconColor?: string;
};

/**
 * The type of the menu.
 */
export type Menu = {
  title: string;
  routes: MenuRoute[];
};

/**
 * The type of the token.
 */
export type Token = {
  token: string;
  user: User;
};

/**
 * The type of the user.
 */
export type User = {
  id: number;
  created_at: string;
  updated_at: string;
  login_id: string;
  username: string;
  avatar: string | null;
  role: keyof typeof UserRole;
  preferences: {
    homepage: string;
    vibration: boolean;
    recent_searches: boolean;
    recent_watches: boolean;
    search_records: number;
    watch_records: number;
    landscape_mode: 'rotate' | 'web_api';
    [key: string]: string | boolean | number;
  } | null;
  user_agent: string;
  client_ip: string;
  login_at: string;
  expire_at: string;
  last_activity: string | null;
};

/**
 * The type of the file path.
 */
export type Path = {
  name: string;
  path: string;
  is_dir: boolean;
  is_empty: boolean | null;
  file_type: string | null;
  children?: Path[] | null;
  loading?: boolean;
};

/**
 * The type of the file path statistics.
 */
export type PathStats = {
  name: string;
  path: string;
  is_dir: boolean;
  readable: boolean;
  writable: boolean;
  size: string;
  total?: string;
  used?: string;
  free?: string;
};

/**
 * The type of the global variable.
 */
export type GlobalVariable = {
  id: number;
  created_at: string;
  updated_at: string;
  key: string;
  value: string;
  value_length: number;
  encrypted: boolean;
};

/**
 * The type of the media library.
 */
export type MediaLib = {
  id: number;
  lib_type: keyof typeof LibType;
  name: string;
  dir: string;
  language: string | null;
  priority: number;
  triggers: FlowTrigger[];
};

/**
 * The type of the media item.
 */
export type MediaItem = {
  id: number;
  lib_id: number;
  dir: string;
  path: string;
  name: string;
  meta: string | null;
  title: string | null;
  cover: string | null;
  backdrop: string | null;
  year: number | null;
  rating: string | null;
};

/**
 * The type of the download manager.
 */
export type Downloader = {
  id: number;
  created_at: string;
  updated_at: string;
  preset: string | null;
  config: string;
  name: string;
  host: string | null;
  port: number | null;
  version: string | null;
  priority: number;
  status: 'up' | 'down' | 'unknown';
  triggers: FlowTrigger[];
};

/**
 * The type of the download directory.
 */
export type DownloadDir = {
  path: string;
  free: string;
};

/**
 * The type of the download task.
 */
export type DownloadTask = {
  id: number;
  downloader_id: number;
  dir: string;
  name: string;
  unique_id: string | null;
  info_hash: string | null;
  info_hash_v2: string | null;
  magnet_link: string | null;
  state: keyof typeof DownloadState;
  raw_state: string | null;
  error_msg: string | null;
  up_speed: number | null;
  dl_speed: number | null;
  percentage: number | null;
  total_size: number | null;
  completed_size: number | null;
  completed_at: string | null;
  ratio: string;
  estimate: string;
};

/**
 * The type of the flow repository.
 */
export type FlowRepo = {
  id: number;
  created_at: string;
  updated_at: string;
  repo_name: string;
  repo_url: string;
  repo_description: string | null;
  owner_name: string | null;
  owner_url: string | null;
  owner_avatar: string | null;
  loading?: boolean;
};

/**
 * The type of the flow template.
 */
export type FlowTemplate = {
  id: number;
  created_at: string;
  updated_at: string;
  repo: FlowRepo;
  path: string;
  name: string;
  icon: string | null;
  description: string | null;
  category: keyof typeof GraphCategory;
  revision: number;
  definition: {
    nodes: Node[];
    edges: Edge[];
  };
  newest: boolean;
  graphs: FlowGraph[];
};

/**
 * The type of the flow graph.
 */
export type FlowGraph = {
  id: number;
  created_at: string;
  updated_at: string;
  tmpl_id: number | null;
  name: string;
  icon: string | null;
  description: string | null;
  category: keyof typeof GraphCategory;
  revision: number | null;
  state: keyof typeof GraphState;
  draft: {
    nodes: Node[];
    edges: Edge[];
  } | null;
  editable: boolean;
  success_rate: number | null;
  average_time: number | null;
  last_execution: string | null;
  node_types: string[];
  tmpl: FlowTemplate | null;
};

/**
 * The type of the flow graph context.
 */
export type FlowGraphContext = {
  validators: Set<() => boolean>;
  addValidator: (validator: () => boolean) => void;
};

/**
 * The type of the flow execution log.
 */
export type FlowLog = {
  at: string;
  type: string;
  data: Record<string, any> | null; // eslint-disable-line
  document: any; // eslint-disable-line
};

/**
 * The type of the flow trigger.
 */
export type FlowTrigger = {
  id?: number | null;
  graph_id: number;
  graph_name: string;
  asynchronous: boolean;
};

/**
 * The type of the node handle.
 */
export type Handle = {
  id: string;
  handle_type: HandleType;
  position: Position;
  maxconn: number;
  style: string | null;
  tag: string | null;
};

/**
 * The type of the node field.
 */
export type Field = {
  id: string;
  field_type: string;
  label: string | null;
  tooltip: string | null;
  required: boolean;
  default: any; // eslint-disable-line
};

/**
 * The type of the node schema.
 */
export type NodeSchema = {
  node_type: string;
  name: string;
  icon: keyof typeof icons;
  group: string;
  order: number;
  fields: Field[];
  handles: Handle[];
};

/**
 * The type of the resource section.
 */
export type Section = {
  id?: string | null;
  url?: string | null;
  title: string;
  definition?: boolean;
};

/**
 * The type of the video definition.
 */
export type Definition = {
  url: string;
  definition: string | number;
};

/**
 * The type of the danmaku.
 */
export type Danmaku = {
  id?: string | null;
  text: string;
  mode?: 'scroll' | 'top' | 'bottom' | null;
  color?: string | null;
  start?: number | null;
  duration?: number | null;
};

/**
 * The type of the view mode.
 */
export type ViewMode = 'table' | 'grid';
export type ViewModes = [ViewMode, ...ViewMode[]];

/**
 * The type of the websearch result.
 */
export type Resource = Optional<{
  id: string;
  title: string;
  cover: string;
  link: string;
  size: string;
  brief: string;
  category: string;
  uploader: string;
  uploaded_at: string;
  media_type: 'video' | 'audio' | 'image' | 'text';
  video_type: 'mp4' | 'flv' | 'hls';
  url: string;
  text: string;
  images: string[];
  definitions: Definition[];
  sections: Section[];
  danmakus: Danmaku[];
  favorite: boolean;
}>;

/**
 * The type of the favorite resource.
 */
export type Favorite = {
  id: number;
  created_at: string;
  updated_at: string;
  user_id: number;
  indexer_id: number;
  rsrc_id: string;
  rsrc: Resource;
  url: string | null;
};

/**
 * The type of the search filter.
 */
export type Filter = {
  type: 'text' | 'radio' | 'checkbox' | 'calendar';
  label?: string;
  options?: Record<string, string>;
};

/**
 * The type of the indexer authentication.
 */
export type IndexerAuth = { name?: string | null } | null;

/**
 * The type of the indexer configuration.
 */
export type IndexerConfig = Optional<{
  auth: Optional<{
    login: Optional<{
      mode: string;
      required: boolean;
    }>;
    cookie: Optional<{
      domain: string;
      path: string;
      name: string;
    }>;
  }>;

  search: Optional<{
    display: Optional<{
      page_size: number;
      view_modes: string[];
      cover_ratio: string;
    }>;
    keyword: Optional<{
      global: boolean;
      required: boolean;
    }>;
    filters: Record<string, Filter>;
  }>;

  board: Optional<{
    display: Optional<{
      view_modes: string[];
      cover_ratio: string;
    }>;
  }>;

  details: Optional<{
    specific: Optional<{
      media_type: string;
      video_type: string;
    }>;
  }>;
}>;
