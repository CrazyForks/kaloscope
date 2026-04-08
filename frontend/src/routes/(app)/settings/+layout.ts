import { getCurrentRole } from '$lib/api';
import { icons } from '$lib/icons';
import type { Menu } from '$lib/types';
import type { LayoutLoad } from './$types';

export const load: LayoutLoad = async () => {
  const menus: Menu[] = [
    {
      title: 'nav.settings.personal.title',
      routes: [
        {
          title: 'nav.settings.personal.account',
          path: '/settings/personal/account',
          icon: icons.lockClosedKey
        },
        {
          title: 'nav.settings.personal.sessions',
          path: '/settings/personal/sessions',
          icon: icons.phoneTablet
        },
        {
          title: 'nav.settings.personal.preferences',
          path: '/settings/personal/preferences',
          icon: icons.textGrammarSettings
        }
      ]
    },
    {
      title: 'nav.settings.help.title',
      routes: [
        {
          title: 'nav.settings.help.doc',
          path: 'https://github.com/kaloscope/kaloscope',
          icon: icons.bookQuestionMark
        },
        {
          title: 'nav.settings.help.issue',
          path: 'https://github.com/kaloscope/kaloscope/issues',
          icon: icons.chatWarning
        }
      ]
    }
  ];

  if ((await getCurrentRole()) === 'admin') {
    menus.splice(
      1,
      0,
      {
        title: 'nav.settings.system.title',
        routes: [
          {
            title: 'nav.settings.system.users',
            path: '/settings/system/users',
            icon: icons.peopleSettings
          },
          {
            title: 'nav.settings.system.variables',
            path: '/settings/system/variables',
            icon: icons.bracesVariable
          },
          {
            title: 'nav.settings.system.medialibs',
            path: '/settings/system/medialibs',
            icon: icons.videoClipMultiple
          },
          {
            title: 'nav.settings.system.downloaders',
            path: '/settings/system/downloaders',
            icon: icons.box3dDownload
          }
        ]
      },
      {
        title: 'nav.settings.workflows.title',
        routes: [
          {
            title: 'nav.settings.workflows.templates',
            path: '/settings/workflows/templates',
            icon: icons.appStore
          },
          {
            title: 'nav.settings.workflows.graphs',
            path: '/settings/workflows/graphs',
            icon: icons.documentFlowchart
          },
          {
            title: 'nav.settings.workflows.schedule',
            path: '/settings/workflows/schedule',
            icon: icons.clock
          }
        ]
      }
    );
  }

  return { menus };
};
