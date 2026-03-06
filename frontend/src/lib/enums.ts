import { icons } from '$lib/icons';
import type { IconifyIcon } from 'iconify-icon';

/**
 * Enum type.
 */
export type Enum = {
  [key: string]: {
    label: string;
    icon: IconifyIcon | null;
    iconColor: string | null;
  };
};

/**
 * Create an enum.
 *
 * @param object - The object to convert to an enum.
 * @returns The enum.
 */
export function createEnum<T extends Enum>(object: T): T {
  return object;
}

/**
 * Convert an enum to an array of options for a select input.
 *
 * @param enumObject - The enum to convert.
 * @param enumAll - Whether to include an `all` option.
 * @returns An array of options.
 */
export function enumToOptions(enumObject: Enum, enumAll: boolean = true): { value: string; label: string }[] {
  const options = Object.entries(enumObject).map(([value, { label }]) => ({ value, label }));
  if (enumAll) {
    options.unshift({ value: '', label: 'enum.all' });
  }
  return options;
}

export const UserRole = createEnum({
  user: {
    label: 'enum.user_role.user',
    icon: icons.user,
    iconColor: null
  },
  admin: {
    label: 'enum.user_role.admin',
    icon: icons.keyFilled,
    iconColor: null
  }
});

export const LibType = createEnum({
  movie: {
    label: 'enum.lib_type.movie',
    icon: icons.moviesAndTv,
    iconColor: null
  },
  tv_show: {
    label: 'enum.lib_type.tv_show',
    icon: icons.deviceTvOld,
    iconColor: null
  },
  music: {
    label: 'enum.lib_type.music',
    icon: icons.musicSquare,
    iconColor: null
  }
});

export const GraphCategory = createEnum({
  indexer: {
    label: 'enum.graph_category.indexer',
    icon: icons.globeSearch,
    iconColor: null
  },
  ingest: {
    label: 'enum.graph_category.ingest',
    icon: icons.library,
    iconColor: null
  },
  schedule: {
    label: 'enum.graph_category.schedule',
    icon: icons.clock,
    iconColor: null
  }
});

export const GraphState = createEnum({
  drafting: {
    label: 'enum.graph_state.drafting',
    icon: icons.selectObjectSkewEdit,
    iconColor: null
  },
  modified: {
    label: 'enum.graph_state.modified',
    icon: icons.checkmarkCircle,
    iconColor: null
  },
  published: {
    label: 'enum.graph_state.published',
    icon: icons.checkmarkCircle,
    iconColor: 'var(--color-success)'
  }
});

export const DownloadState = createEnum({
  downloading: {
    label: 'enum.download_state.downloading',
    icon: icons.download,
    iconColor: null
  },
  paused: {
    label: 'enum.download_state.paused',
    icon: icons.pauseFilled,
    iconColor: null
  },
  completed: {
    label: 'enum.download_state.completed',
    icon: icons.fileCheck,
    iconColor: 'var(--color-success)'
  },
  error: {
    label: 'enum.download_state.error',
    icon: icons.dismissCircle,
    iconColor: 'var(--color-error)'
  }
});
