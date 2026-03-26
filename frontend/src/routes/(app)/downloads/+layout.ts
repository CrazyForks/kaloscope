import { icons } from '$lib/icons';
import type { Menu } from '$lib/types';
import type { LayoutLoad } from './$types';

export const load: LayoutLoad = () => {
  const menus: Menu[] = [
    {
      title: 'nav.downloads.title',
      routes: [
        {
          title: 'nav.downloads.plans',
          path: '/downloads/plans',
          icon: icons.arrowRotateClockwise
        },
        {
          title: 'nav.downloads.downloading',
          path: '/downloads/downloading',
          icon: icons.download
        },
        {
          title: 'nav.downloads.completed',
          path: '/downloads/completed',
          icon: icons.fileCheck
        }
      ]
    },
    {
      title: 'nav.downloads.speed.title',
      routes: [
        {
          title: 'nav.downloads.speed.up',
          icon: icons.arrowBigUp,
          iconColor: 'var(--color-info)'
        },
        {
          title: 'nav.downloads.speed.dl',
          icon: icons.arrowBigDown,
          iconColor: 'var(--color-success)'
        }
      ]
    }
  ];
  return { menus };
};
