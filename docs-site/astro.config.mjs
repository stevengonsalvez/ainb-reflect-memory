import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';
import starlightLinksValidator from 'starlight-links-validator';

export default defineConfig({
  site: 'https://stevengonsalvez.github.io',
  base: '/ainb-reflect-memory/',
  trailingSlash: 'always',
  integrations: [
    starlight({
      plugins: [starlightLinksValidator({ errorOnInvalidHashes: true })],
      title: 'reflect',
      description: 'Self-improving memory for coding agents: capture, drain, index, recall.',
      social: [{ icon: 'github', label: 'GitHub', href: 'https://github.com/stevengonsalvez/ainb-reflect-memory' }],
      logo: {
        light: './src/assets/logo-light.svg',
        dark: './src/assets/logo-dark.svg',
        alt: 'reflect',
      },
      head: [
        { tag: 'meta', attrs: { name: 'theme-color', content: '#0f0f18' } },
        { tag: 'meta', attrs: { property: 'og:image', content: 'https://stevengonsalvez.github.io/ainb-reflect-memory/img/og.png' } },
        { tag: 'meta', attrs: { property: 'og:image:width', content: '1200' } },
        { tag: 'meta', attrs: { property: 'og:image:height', content: '630' } },
        { tag: 'meta', attrs: { name: 'twitter:card', content: 'summary_large_image' } },
        { tag: 'meta', attrs: { name: 'twitter:image', content: 'https://stevengonsalvez.github.io/ainb-reflect-memory/img/og.png' } },
      ],
      lastUpdated: true,
      expressiveCode: {
        themes: ['night-owl', 'github-light'],
        styleOverrides: {
          borderRadius: '8px',
          codeBackground: ({ theme }) => (theme.type === 'dark' ? '#0a0a12' : '#f6f6fa'),
          codeFontFamily: "ui-monospace, 'SF Mono', 'JetBrains Mono', 'Cascadia Code', Menlo, Consolas, 'DejaVu Sans Mono', monospace",
        },
      },
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
