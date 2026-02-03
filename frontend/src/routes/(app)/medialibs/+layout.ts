import { api } from '$lib/api';
import { LibType } from '$lib/enums';
import type { MediaLib, Menu, Resp } from '$lib/types';
import type { LayoutLoad } from './$types';

export const load: LayoutLoad = async () => {
  const menus: Menu[] = [];

  await api
    .get('media/lib/list')
    .json<Resp<MediaLib[]>>()
    .then((resp) => {
      if (resp.data.length > 0) {
        menus.push({
          title: 'nav.medialibs.title',
          routes: resp.data.map((lib) => ({
            title: lib.name,
            path: `/medialibs/${lib.id}`,
            icon: LibType[lib.lib_type].icon
          }))
        });
      }
    });

  return { menus };
};
