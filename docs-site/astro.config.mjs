import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';

export default defineConfig({
  site: 'https://stevengonsalvez.github.io',
  base: '/ainb-reflect-memory/',
  trailingSlash: 'always',
  integrations: [
    starlight({
      title: 'reflect',
      description: 'Self-improving memory for coding agents: capture, drain, index, recall.',
      social: [{ icon: 'github', label: 'GitHub', href: 'https://github.com/stevengonsalvez/ainb-reflect-memory' }],
      customCss: ['./src/styles/custom.css'],
      editLink: { baseUrl: 'https://github.com/stevengonsalvez/ainb-reflect-memory/edit/main/docs-site/' },
      sidebar: [
        { label: 'Start here', items: [{ autogenerate: { directory: 'start' } }] },
        { label: 'Install per harness', items: [{ autogenerate: { directory: 'install' } }] },
        { label: 'Concepts', items: [{ autogenerate: { directory: 'concepts' } }] },
        { label: 'Interactive', items: [{ autogenerate: { directory: 'interactive' } }] },
        { label: 'Guides', items: [{ autogenerate: { directory: 'guides' } }] },
        { label: 'Reference', items: [{ autogenerate: { directory: 'reference' } }] },
        { label: 'Evals and benchmarks', items: [{ autogenerate: { directory: 'evals' } }] },
        { label: 'Design notes', items: [{ autogenerate: { directory: 'design' } }] },
        { label: 'Changelog', link: '/changelog/' },
      ],
    }),
  ],
});
