import { EMPTY, EMPTY_SIGN } from '$lib/constants';
import { date as _date, format as _format, time as _time } from 'svelte-i18n';
import { derived } from 'svelte/store';

export { locale, locales, number } from 'svelte-i18n';
export { format as _ };

type Nullable<T> = T | null | undefined;

/**
 * Formatter for i18n messages.
 */
export const format = derived(_format, ($format) => {
  return (
    id: Nullable<string>,
    values?: string | number | (string | number)[] | { locale?: string; default?: string }
  ) => {
    if (id === null || id === undefined) {
      return EMPTY;
    }
    let options;
    if (values !== null && typeof values === 'object' && !Array.isArray(values)) {
      options = values;
    } else if (typeof values === 'string' || typeof values === 'number') {
      options = { values: { 0: values } };
    } else if (Array.isArray(values) && values.length > 0) {
      // convert array to object with numeric keys
      options = {
        values: values.reduce(
          (acc, value, index) => {
            acc[`${index}`] = value;
            return acc;
          },
          {} as Record<string, string | number>
        )
      };
    }
    return $format(id, options);
  };
});

/**
 * Format date with `2-digit` month, day, and `numeric` year.
 */
export const date = derived(_date, ($date) => {
  return (value: Nullable<number | string | Date>) => {
    if (value === null || value === undefined) {
      return EMPTY_SIGN;
    }
    return $date(new Date(value), { month: '2-digit', day: '2-digit', year: 'numeric' });
  };
});

/**
 * Format time with `2-digit` hour, minute, and second.
 */
export const time = derived(_time, ($time) => {
  return (value: Nullable<number | string | Date>) => {
    if (value === null || value === undefined) {
      return EMPTY_SIGN;
    }
    return $time(new Date(value), { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  };
});

/**
 * Format date and time, separated by a space.
 */
export const dateTime = derived([date, time], ([$date, $time]) => {
  return (value: Nullable<number | string | Date>) => {
    if (value === null || value === undefined) {
      return EMPTY_SIGN;
    }
    return `${$date(value)} ${$time(value)}`;
  };
});

/**
 * Format duration in milliseconds to a human-readable string.
 */
export const milliseconds = derived(_format, ($format) => {
  return (milliseconds: Nullable<number>) => {
    if (milliseconds === null || milliseconds === undefined) {
      return EMPTY_SIGN;
    }
    if (milliseconds < 1000) {
      return `${Math.floor(milliseconds)} ${$format('duration.milliseconds').toLowerCase()}`;
    }
    const seconds = milliseconds / 1000;
    if (seconds < 60) {
      return `${Math.floor(seconds)} ${$format('duration.seconds').toLowerCase()}`;
    }
    const minutes = seconds / 60;
    if (minutes < 60) {
      return `${Math.floor(minutes)} ${$format('duration.minutes').toLowerCase()}`;
    }
    const hours = minutes / 60;
    if (hours < 24) {
      return `${Math.floor(hours)} ${$format('duration.hours').toLowerCase()}`;
    }
    const days = hours / 24;
    return `${Math.floor(days)} ${$format('duration.days').toLowerCase()}`;
  };
});

/**
 * Format duration between two dates in milliseconds.
 */
export const duration = derived(milliseconds, ($milliseconds) => {
  return (start: Nullable<string>, end?: Nullable<string>) => {
    if (!start) {
      return $milliseconds(0);
    }
    const endDate = end ? new Date(end) : new Date();
    return $milliseconds(Math.abs(endDate.getTime() - new Date(start).getTime()));
  };
});

/**
 * Format head title with app name.
 */
export const headTitle = derived(_format, ($format) => {
  return (key: string) => {
    const title = $format(key);
    if (window.matchMedia('(display-mode: standalone)').matches) {
      return title;
    }
    const appName = $format('app.name');
    return title ? `${title} | ${appName}` : appName;
  };
});

/**
 * Formatter for node labels, tooltips, and placeholders.
 */
export const nodeFormatter = {
  label: derived(_format, ($format) => {
    return (key: Nullable<string>) => $format(`flow.node.label.${key}`, { default: key ?? '' });
  }),
  tooltip: derived(_format, ($format) => {
    return (key: Nullable<string>) => $format(`flow.node.tooltip.${key}`, { default: key ?? '' });
  }),
  placeholder: derived(_format, ($format) => {
    return (key: Nullable<string>) => $format(`flow.node.placeholder.${key}`, { default: key ?? '' });
  })
};
