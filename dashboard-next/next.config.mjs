/** @type {import('next').NextConfig} */
const nextConfig = {
  webpack: (config, { isServer }) => {
    if (isServer) {
      // sql.js WASM needs to be treated as an asset on the server
      config.externals = [...(config.externals || []), "sql.js"];
    }
    return config;
  },
};

export default nextConfig;
