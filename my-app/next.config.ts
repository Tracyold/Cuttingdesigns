import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  turbopack: {
    resolveExtensions: ['.matr', '.life', '.tsx', '.ts', '.jsx', '.js'],
    rules: {
      '*.matr': {
        as: '*.tsx',
      },
      '*.life': {
        as: '*.tsx',
      },
      '*.loc': {
        loaders: ['sass-loader'],
        as: '*.scss',
      },
      '*.dec': {
        loaders: ['sass-loader'],
        as: '*.scss',
      },
      '*.eff': {
        loaders: ['sass-loader'],
        as: '*.scss',
      },
      '*.root': {
        loaders: ['sass-loader'],
        as: '*.scss',
      },
    },
  },
};

export default nextConfig;
